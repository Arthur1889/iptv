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
    handlers=[
        logging.FileHandler("crawl.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def ensure_dependencies():
    required = ["requests", "tqdm"]
    for lib in required:
        try: __import__(lib)
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", lib])

ensure_dependencies()
import requests
from tqdm import tqdm
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 1. 配置与分类规则 =================
CONFIG_FILE = "sources.json"
NAME_JSON = "name.json"
BLACKLIST_FILE = "blacklist.json"

# 7. 频道排序优先级
GROUP_ORDER = [
    "4K频道", "央视频道", "地方卫视", "港澳台", "山东频道", "数字频道", 
    "影视频道", "纪录纪实", "娱乐频道", "少儿动画", "体育赛事", "综合频道"
]
GROUP_PRIORITY = {name: i for i, name in enumerate(GROUP_ORDER)}

def get_env_config():
    sys_type = platform.system()
    # 采用平衡配置：20秒超时，40线程
    conf = {"os": sys_type, "ffprobe": "ffprobe", "ffmpeg": "ffmpeg", "timeout": 20, "workers": 40}
    if sys_type == "Windows":
        for p in [r"C:\ffmpeg\bin", r"D:\ffmpeg\bin"]:
            if os.path.exists(os.path.join(p, "ffmpeg.exe")):
                conf.update({"ffmpeg": os.path.join(p, "ffmpeg.exe"), "ffprobe": os.path.join(p, "ffprobe.exe")})
                break
    return conf

ENV = get_env_config()

# ================= 2. 黑名单与工具函数 =================
def load_blacklist():
    if os.path.exists(BLACKLIST_FILE):
        try:
            with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f: return json.load(f)
        except: return {}
    return {}

def get_group(name, sid, height):
    n = name.upper()
    if height >= 2160 or "4K" in n: return "4K频道"
    if "CCTV" in n: return "央视频道"
    if "卫视" in n:
        if any(k in n for k in ["凤凰", "TVB", "翡翠", "亚洲", "中视", "华视"]): return "港澳台"
        return "地方卫视"
    if "山东" in n: return "山东频道"
    if any(k in n for k in ["CHC", "电影", "剧场", "私人", "影院"]): return "影视频道"
    if any(k in n for k in ["纪录", "纪实", "探索", "求索", "地理"]): return "纪录纪实"
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
    bl_data = load_blacklist()
    stats = {"raw": 0, "bl_skip": 0, "catvod_drop": 0, "junk_drop": 0, "ffmpeg_drop": 0, "passed": 0}
    
    # 加载别名
    alias_map = {}
    if os.path.exists(NAME_JSON):
        with open(NAME_JSON, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split(',')
                if len(parts) >= 2: alias_map[parts[0]] = parts[1:]

    # 1. 初始源梳理
    if not os.path.exists(CONFIG_FILE): return
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        urls = json.load(f).get("urls", [])

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
                    
                    # 5 & 6. 关键词与 catvod 过滤
                    if "catvod.com" in link: stats["catvod_drop"] += 1; continue
                    if any(k in name for k in ["直播室", "轮播", "专题", "课堂"]): stats["junk_drop"] += 1; continue
                    
                    # 黑名单过滤 (3次且24h内)
                    if link in bl_data and bl_data[link]['fail_count'] >= 3:
                        last_fail = datetime.strptime(bl_data[link]['last_fail'], "%Y-%m-%d %H:%M:%S")
                        if datetime.now() - last_fail < timedelta(hours=24):
                            stats["bl_skip"] += 1; continue
                    
                    if not link.startswith('http') or link in seen_urls: continue
                    all_channels.append({"name": name, "url": link})
                    seen_urls.add(link)
        except: continue
    
    candidate_pool = {}
    # 1. 进度条：含时间、速度、优质源
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(deep_analyze_stream, ch['url']): ch for ch in all_channels}
        with tqdm(total=len(all_channels), desc="[正在探测]", unit="ch", bar_format='{l_bar}{bar:20}{r_bar} 优质:{postfix}') as pbar:
            for f in as_completed(futures):
                ch = futures[f]
                h, br = f.result()
                if h > 0:
                    if ch['url'] in bl_data: del bl_data[ch['url']]
                    stats["passed"] += 1
                    ch.update({'h': h, 'br': br, 'score': h * 1000 + br/1000})
                    # 自动分类
                    group = get_group(ch['name'], "", h)
                    candidate_pool.setdefault(group, {}).setdefault(ch['name'], []).append(ch)
                else:
                    stats["ffmpeg_drop"] += 1
                    info = bl_data.get(ch['url'], {"fail_count": 0})
                    info.update({"fail_count": info["fail_count"] + 1, "last_fail": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                    bl_data[ch['url']] = info
                pbar.set_postfix_str(str(stats["passed"]))
                pbar.update(1)

    # 4. 择优逻辑 (720P 准入 + 核心保底)
    final_output = []
    for g_name in GROUP_ORDER:
        if g_name not in candidate_pool: continue
        channels_in_group = candidate_pool[g_name]
        for ch_name, sources in channels_in_group.items():
            sources.sort(key=lambda x: x['score'], reverse=True)
            best = sources[0]
            is_core = g_name in ["央视频道", "地方卫视"]
            if is_core or best['h'] >= 720:
                final_output.append({"group": g_name, "name": ch_name, "url": best['url'], "h": best['h']})

    # 写入 M3U
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_output:
            f.write(f'#EXTINF:-1 group-title="{ch["group"]}",{ch["name"]}\n{ch["url"]}\n')

    with open(BLACKLIST_FILE, 'w', encoding='utf-8') as f: json.dump(bl_data, f, indent=2)

    # 2. 摘要报告
    duration = int(time.time() - start_time)
    logger.info(f"""
==================================================
              IPTV 探测任务摘要报告
==================================================
1. 初始源梳理:
   - 抓取总数: {stats['raw']}
   - 过滤 CatVod: {stats['catvod_drop']}
   - 过滤直播室/杂质: {stats['junk_drop']}
   - 黑名单跳过: {stats['bl_skip']}

2. 过滤情况:
   - 探测失败/死链: {stats['ffmpeg_drop']}
   - 质量过滤: 非核心频道 < 720P 已自动剔除

3. 最终情况:
   - 入选总数: {len(final_output)} 个频道
   - 频道分类: 已按指定 12 类顺序排序
   - 总耗时: {duration // 60}分{duration % 60}秒
   - 日志文件: crawl.log | 黑名单: {BLACKLIST_FILE}
==================================================
""")

if __name__ == "__main__":
    run()