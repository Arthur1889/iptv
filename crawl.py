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
BLACKLIST_FILE = "blacklist.json"

# 7. 频道分组排序顺序
GROUP_ORDER = [
    "4K频道", "央视频道", "地方卫视", "港澳台", "山东频道", "数字频道", 
    "影视频道", "纪录纪实", "娱乐频道", "少儿动画", "体育赛事", "歌曲及音乐MV", "综合频道"
]
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

def clean_name(name):
    """ 10. 清除名称中的杂质字眼 """
    # 移除质量字眼、地理限制、在线时间等
    noise = r'(HD|高清|超高清|蓝光|BD|\d+P|K|Not 24/7|Geo-blocked|\[.*?\]|\(.*?\)|\-)'
    name = re.sub(noise, '', name, flags=re.I)
    # 移除末尾多余空格和特殊符号
    return name.strip().rstrip('-').strip()

def get_group(name, height):
    """ 11. 4K频道分组逻辑限定 """
    n = name.upper()
    is_cv = any(k in n for k in ["CCTV", "卫视"])
    
    # 4K频道只放央视和卫视的4K/8K
    if height >= 2160 and is_cv: return "4K频道"
    
    if "CCTV" in n: return "央视频道"
    if "卫视" in n:
        if any(k in n for k in ["凤凰", "TVB", "翡翠", "亚洲", "中视", "华视", "凤凰", "星空"]): return "港澳台"
        return "地方卫视"
    if "山东" in n: return "山东频道"
    if any(k in n for k in ["电影", "影院", "CHC", "剧场", "私人"]): return "影视频道"
    if any(k in n for k in ["纪录", "纪实", "探索", "求索", "地理"]): return "纪录纪实"
    if any(k in n for k in ["歌曲", "音乐", "MV", "DJ", "演唱会"]): return "歌曲及音乐MV"
    if any(k in n for k in ["少儿", "卡通", "动漫", "动画", "卡酷"]): return "少儿动画"
    if any(k in n for k in ["体育", "足球", "竞赛", "竞技", "五星", "劲爆"]): return "体育赛事"
    if any(k in n for k in ["综艺", "娱乐", "点播"]): return "娱乐频道"
    if any(k in n for k in ["风云", "第一", "女性", "兵器", "文化", "求索"]): return "数字频道"
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
        try:
            with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f: bl_data = json.load(f)
        except: bl_data = {}

    stats = {"raw": 0, "bl_skip": 0, "catvod_drop": 0, "junk_drop": 0, "ffmpeg_drop": 0, "passed": 0}
    
    if not os.path.exists(CONFIG_FILE): return
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f: urls = json.load(f).get("urls", [])

    all_channels, seen_urls = [], set()
    for url in urls:
        try:
            r = requests.get(url, timeout=10, verify=False)
            lines = r.text.split('\n')
            for i in range(len(lines)):
                if lines[i].startswith('#EXTINF:'):
                    raw_name = lines[i].split(',')[-1].strip()
                    link = lines[i+1].strip() if i+1 < len(lines) else ""
                    stats["raw"] += 1
                    
                    # 6. 过滤 catvod.com
                    if "catvod.com" in link: stats["catvod_drop"] += 1; continue
                    # 5. 过滤直播室
                    if any(k in raw_name for k in ["直播室", "轮播", "专题", "课堂", "测试"]): stats["junk_drop"] += 1; continue
                    
                    # 黑名单过滤
                    if link in bl_data and bl_data[link].get('fail_count', 0) >= 3:
                        last_fail = datetime.strptime(bl_data[link]['last_fail'], "%Y-%m-%d %H:%M:%S")
                        if datetime.now() - last_fail < timedelta(hours=24): stats["bl_skip"] += 1; continue
                    
                    if not link.startswith('http') or link in seen_urls: continue
                    all_channels.append({"name": raw_name, "url": link})
                    seen_urls.add(link)
        except: continue
    
    # { "频道ID": { "High": [源...], "Std": [源...] } }
    unified_pool = {}

    # 1. 进度条：含有时间、速度、优质源
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(deep_analyze_stream, ch['url']): ch for ch in all_channels}
        with tqdm(total=len(all_channels), desc="[探测进度]", unit="ch", bar_format='{l_bar}{bar:20}{r_bar} 优质:{postfix}') as pbar:
            for f in as_completed(futures):
                ch = futures[f]
                h, br = f.result()
                if h > 0:
                    if ch['url'] in bl_data: del bl_data[ch['url']]
                    stats["passed"] += 1
                    
                    # 10. 强力清洗名称
                    s_name = clean_name(ch['name'])
                    
                    # 生成 sid (用于去重)
                    cctv_match = re.search(r'(CCTV[-]?\d+[\+]?)', s_name, re.I)
                    sid = cctv_match.group(1).upper().replace('-', '') if cctv_match else s_name
                    
                    ch.update({'h': h, 'br': br, 'score': h * 1000 + br/1000, 's_name': s_name, 'sid': sid})
                    
                    # 8. 双桶去重池
                    unified_pool.setdefault(sid, {"High": [], "Std": []})
                    if h >= 2160: unified_pool[sid]["High"].append(ch)
                    else: unified_pool[sid]["Std"].append(ch)
                else:
                    stats["ffmpeg_drop"] += 1
                    info = bl_data.get(ch['url'], {"fail_count": 0})
                    info.update({"fail_count": info["fail_count"] + 1, "last_fail": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                    bl_data[ch['url']] = info
                pbar.set_postfix_str(str(stats["passed"]))
                pbar.update(1)

    # 择优与去重处理
    final_list = []
    for sid, buckets in unified_pool.items():
        for b_type in ["High", "Std"]:
            sources = buckets[b_type]
            if not sources: continue
            # 选出该桶内分数最高的一个源
            sources.sort(key=lambda x: x['score'], reverse=True)
            best = sources[0]
            
            group = get_group(best['s_name'], best['h'])
            
            # 4. 准入规则 (720P 或 核心保底)
            is_core = any(k in group for k in ["央视", "卫视"])
            if is_core or best['h'] >= 720:
                final_list.append({"group": group, "name": best['s_name'], "url": best['url'], "h": best['h'], "sid": sid})

    # 9. 央视按照 1-17 排序
    def sort_key(x):
        g_pri = GROUP_PRIORITY.get(x['group'], 99)
        cctv_num = 999
        if "央视频道" in x['group'] or "4K频道" in x['group']:
            match = re.search(r'CCTV(\d+)', x['sid'], re.I)
            if match: cctv_num = int(match.group(1))
            elif "4K" in x['name']: cctv_num = 0
        return (g_pri, cctv_num, x['name'])

    final_list.sort(key=sort_key)

    # 写入文件
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            f.write(f'#EXTINF:-1 group-title="{ch["group"]}",{ch["name"]}\n{ch["url"]}\n')

    with open(BLACKLIST_FILE, 'w', encoding='utf-8') as f: json.dump(bl_data, f, indent=2)

    # 2. 摘要报告
    duration = int(time.time() - start_time)
    logger.info(f"""
==================================================
              IPTV 探测任务摘要报告
==================================================
1. 初始源梳理:
   - 抓取总数: {stats['raw']} | 屏蔽 CatVod: {stats['catvod_drop']}
   - 黑名单跳过: {stats['bl_skip']} | 关键词过滤: {stats['junk_drop']}

2. 过滤与去重:
   - 探测失败: {stats['ffmpeg_drop']}
   - 重复处理: 已执行双桶择优（每频道 4K/标清 各留一）
   - 名称清洗: 已剥离 HD/1080P/Geo-blocked 等后缀

3. 最终情况:
   - 入选总数: {len(final_list)} 个
   - 排序规则: 12类分组 + 央视 1-17 精准排序
   - 总耗时: {duration // 60}分{duration % 60}秒
==================================================
""")

if __name__ == "__main__":
    run()