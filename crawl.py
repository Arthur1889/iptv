import os, platform, subprocess, sys, json, re, time, ssl, warnings, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

# ================= 0. 环境与日志配置 =================
warnings.filterwarnings("ignore")
os.environ["WERKZEUG_RUN_MAIN"] = "true" 
ssl._create_default_https_context = ssl._create_unverified_context

# 3. 形成 log 文件
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("crawl.log", encoding="utf-8"), logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def ensure_dependencies():
    for lib in ["requests", "tqdm"]:
        try: __import__(lib)
        except ImportError: subprocess.check_call([sys.executable, "-m", "pip", "install", lib])

ensure_dependencies()
import requests
from tqdm import tqdm
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 1. 配置与排序逻辑 =================
CONFIG_FILE = "sources.json"
NAME_JSON = "name.json"
BLACKLIST_FILE = "blacklist.json"

# 7. 频道分组排序顺序
GROUP_ORDER = ["4K频道", "央视频道", "地方卫视", "港澳台", "山东频道", "数字频道", "影视频道", "纪录纪实", "娱乐频道", "少儿动画", "体育赛事", "综合频道"]
GROUP_PRIORITY = {name: i for i, name in enumerate(GROUP_ORDER)}

def get_env_config():
    sys_type = platform.system()
    conf = {"os": sys_type, "ffprobe": "ffprobe", "ffmpeg": "ffmpeg", "timeout": 20, "workers": 40}
    if sys_type == "Windows":
        for p in [r"C:\ffmpeg\bin", r"D:\ffmpeg\bin"]:
            if os.path.exists(os.path.join(p, "ffmpeg.exe")):
                conf.update({"ffmpeg": os.path.join(p, "ffmpeg.exe"), "ffprobe": os.path.join(p, "ffprobe.exe")})
                break
    return conf

ENV = get_env_config()

# ================= 2. 核心功能函数 =================

def get_group(name, height):
    n = name.upper()
    if height >= 2160 or "4K" in n: return "4K频道"
    if "CCTV" in n: return "央视频道"
    if "卫视" in n:
        if any(k in n for k in ["凤凰", "TVB", "翡翠", "亚洲", "中视", "华视", "港", "澳", "台"]): return "港澳台"
        return "地方卫视"
    if "山东" in n: return "山东频道"
    if any(k in n for k in ["电影", "影院", "CHC", "剧场", "私人"]): return "影视频道"
    if any(k in n for k in ["纪录", "纪实", "探索", "地理"]): return "纪录纪实"
    if any(k in n for k in ["综艺", "娱乐", "音乐", "点播"]): return "娱乐频道"
    if any(k in n for k in ["少儿", "卡通", "动漫", "动画"]): return "少儿动画"
    if any(k in n for k in ["体育", "足球", "竞赛", "竞技"]): return "体育赛事"
    if any(k in n for k in ["风云", "第一", "女性", "兵器", "文化"]): return "数字频道"
    return "综合频道"

def deep_analyze_stream(url):
    cmd = [ENV["ffprobe"], '-v', 'error', '-show_entries', 'stream=width,height,bit_rate', '-of', 'json', '-select_streams', 'v:0', url]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=ENV["timeout"], errors='ignore')
        s = json.loads(res.stdout)['streams'][0]
        return int(s.get('height', 0)), int(s.get('bit_rate', 0))
    except: return 0, 0

# ================= 3. 主程序 =================

def run():
    start_time = time.time()
    bl_data = {}
    if os.path.exists(BLACKLIST_FILE):
        with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f: bl_data = json.load(f)

    stats = {"raw": 0, "bl_skip": 0, "catvod_drop": 0, "junk_drop": 0, "ffmpeg_drop": 0, "passed": 0}
    
    # 1. 初始源梳理
    if not os.path.exists(CONFIG_FILE): return
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f: urls = json.load(f).get("urls", [])

    all_channels, seen_urls = [], set()
    for url in urls:
        try:
            r = requests.get(url, timeout=10, verify=False)
            lines = r.text.split('\n')
            for i in range(len(lines)):
                if lines[i].startswith('#EXTINF:'):
                    name = lines[i].split(',')[-1].strip()
                    link = lines[i+1].strip() if i+1 < len(lines) else ""
                    stats["raw"] += 1
                    # 6. 过滤 catvod.com
                    if "catvod.com" in link: stats["catvod_drop"] += 1; continue
                    # 5. 过滤直播室/轮播
                    if any(k in name for k in ["直播室", "轮播", "专题", "课堂"]): stats["junk_drop"] += 1; continue
                    # 黑名单过滤
                    if link in bl_data and bl_data[link]['fail_count'] >= 3:
                        last_fail = datetime.strptime(bl_data[link]['last_fail'], "%Y-%m-%d %H:%M:%S")
                        if datetime.now() - last_fail < timedelta(hours=24): stats["bl_skip"] += 1; continue
                    
                    if not link.startswith('http') or link in seen_urls: continue
                    all_channels.append({"name": name, "url": link})
                    seen_urls.add(link)
        except: continue
    
    # 建立统一频道池用于去重
    unified_pool = {} # { "标准名": { "High": [], "Std": [] } }

    # 1. 探测进度条：含时间、速度、优质源
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(deep_analyze_stream, ch['url']): ch for ch in all_channels}
        with tqdm(total=len(all_channels), desc="[探测进度]", unit="ch", bar_format='{l_bar}{bar:20}{r_bar} 优质:{postfix}') as pbar:
            for f in as_completed(futures):
                ch = futures[f]
                h, br = f.result()
                if h > 0:
                    if ch['url'] in bl_data: del bl_data[ch['url']]
                    stats["passed"] += 1
                    # 清洗名称作为去重 ID
                    base_name = re.sub(r'(HD|高清|蓝光|超清|\d+P|\[.*?\])', '', ch['name'], flags=re.I).strip()
                    ch.update({'h': h, 'br': br, 'score': h * 1000 + br/1000, 'base_name': base_name})
                    
                    unified_pool.setdefault(base_name, {"High": [], "Std": []})
                    if h >= 2160: unified_pool[base_name]["High"].append(ch)
                    else: unified_pool[base_name]["Std"].append(ch)
                else:
                    stats["ffmpeg_drop"] += 1
                    info = bl_data.get(ch['url'], {"fail_count": 0})
                    info.update({"fail_count": info["fail_count"] + 1, "last_fail": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                    bl_data[ch['url']] = info
                pbar.set_postfix_str(str(stats["passed"]))
                pbar.update(1)

    # 8. 频道去重择优 (4K/非4K各保留一个)
    final_list = []
    for base_name, buckets in unified_pool.items():
        for b_type in ["High", "Std"]:
            sources = buckets[b_type]
            if not sources: continue
            sources.sort(key=lambda x: x['score'], reverse=True)
            best = sources[0]
            group = get_group(best['name'], best['h'])
            
            # 4. 准入规则 (720P 或 核心保底)
            is_core = any(k in group for k in ["央视", "卫视"])
            if is_core or best['h'] >= 720:
                final_list.append({"group": group, "name": base_name, "url": best['url'], "h": best['h']})

    # 9. 央视精准排序逻辑
    def sort_key(x):
        g_pri = GROUP_PRIORITY.get(x['group'], 99)
        cctv_num = 999
        if "央视频道" in x['group']:
            match = re.search(r'CCTV[-]?(\d+)', x['name'], re.I)
            if match: cctv_num = int(match.group(1))
            elif "4K" in x['name'] or "8K" in x['name']: cctv_num = 0 # 4K排最前
        return (g_pri, cctv_num, x['name'])

    final_list.sort(key=sort_key)

    # 写入文件
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            f.write(f'#EXTINF:-1 group-title="{ch["group"]}",{ch["name"]}\n{ch["url"]}\n')

    with open(BLACKLIST_FILE, 'w', encoding='utf-8') as f: json.dump(bl_data, f, indent=2)

    # 2. 任务摘要报告
    duration = int(time.time() - start_time)
    logger.info(f"""
==================================================
              IPTV 探测任务摘要报告
==================================================
1. 初始源梳理:
   - 抓取总数: {stats['raw']} | 独立 URL: {len(all_channels)}
   - 过滤 CatVod: {stats['catvod_drop']} | 过滤垃圾源: {stats['junk_drop']}
   - 黑名单跳过: {stats['bl_skip']}

2. 过滤情况:
   - 探测失败/死链: {stats['ffmpeg_drop']}
   - 质量过滤: 非核心频道且 < 720P 已自动剔除
   - 重复压缩: 已执行 4K/标准 双桶去重

3. 最终情况:
   - 入选频道总数: {len(final_list)} 个
   - 排序规则: 12类分组顺序 + 央视 1-17 精准排序
   - 总耗时: {duration // 60}分{duration % 60}秒
   - 运行日志: crawl.log | 黑名单: {BLACKLIST_FILE}
==================================================
""")

if __name__ == "__main__":
    run()