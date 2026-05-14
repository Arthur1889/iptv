import os, platform, subprocess, sys, json, re, time, ssl, warnings, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

# ================= 0. 配置与环境 =================
warnings.filterwarnings("ignore")
os.environ["WERKZEUG_RUN_MAIN"] = "true" 
ssl._create_default_https_context = ssl._create_unverified_context

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("crawl.log", encoding="utf-8"), logging.StreamHandler(sys.stdout)]
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

# 配置常量
CONFIG_FILE = "sources.json"
NAME_JSON = "name.json"
BLACKLIST_FILE = "blacklist.json"
CCTV_DESC = {"CCTV-1": "CCTV-1 综合", "CCTV-2": "CCTV-2 财经", "CCTV-3": "CCTV-3 综艺", "CCTV-4": "CCTV-4 中文国际", "CCTV-5": "CCTV-5 体育", "CCTV-5+": "CCTV-5+ 体育赛事", "CCTV-6": "CCTV-6 电影", "CCTV-7": "CCTV-7 国防军事", "CCTV-8": "CCTV-8 电视剧", "CCTV-9": "CCTV-9 纪录", "CCTV-10": "CCTV-10 科教", "CCTV-11": "CCTV-11 戏曲", "CCTV-12": "CCTV-12 社会与法", "CCTV-13": "CCTV-13 新闻", "CCTV-14": "CCTV-14 少儿", "CCTV-15": "CCTV-15 音乐", "CCTV-16": "CCTV-16 奥林匹克", "CCTV-17": "CCTV-17 农业农村"}
GROUP_PRIORITY = {"4K频道": 1, "央视频道": 2, "地方卫视": 3, "山东频道": 4, "综合频道": 10}

def get_env_config():
    sys_type = platform.system()
    # 调整配置：timeout 20s, workers 40
    conf = {"os": sys_type, "ffprobe": "ffprobe", "ffmpeg": "ffmpeg", "timeout": 20, "workers": 40}
    if sys_type == "Windows":
        for p in [r"C:\ffmpeg\bin", r"D:\ffmpeg\bin"]:
            if os.path.exists(os.path.join(p, "ffmpeg.exe")):
                conf.update({"ffmpeg": os.path.join(p, "ffmpeg.exe"), "ffprobe": os.path.join(p, "ffprobe.exe")})
                break
    return conf

ENV = get_env_config()

# ================= 1. 黑名单逻辑 =================
def load_blacklist():
    if os.path.exists(BLACKLIST_FILE):
        try:
            with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f: return json.load(f)
        except: return {}
    return {}

def is_blacklisted(url, bl_data):
    if url in bl_data and bl_data[url]['fail_count'] >= 3:
        last_fail = datetime.strptime(bl_data[url]['last_fail'], "%Y-%m-%d %H:%M:%S")
        if datetime.now() - last_fail < timedelta(hours=24): return True
    return False

# ================= 2. 探测与清洗 =================
def get_standard_name(origin_name, alias_map):
    n = origin_name.strip()
    if re.match(r'\d{4}-\d{2}-\d{2}', n) or n.upper() == "CCTV" or len(n) < 2: return "异常源"
    name = re.sub(r'\.(cn|hk|tw|us|uk|org)$', '', n, flags=re.I)
    for main, aliases in alias_map.items():
        if any(a.strip().upper() in name.upper() for a in aliases): return main
    return name

def deep_analyze_stream(url):
    cmd = [ENV["ffprobe"], '-v', 'error', '-show_entries', 'stream=width,height,bit_rate', '-of', 'json', '-select_streams', 'v:0', url]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=ENV["timeout"], errors='ignore')
        s = json.loads(res.stdout)['streams'][0]
        return int(s.get('height', 0)), int(s.get('bit_rate', 0))
    except: return 0, 0

# ================= 3. 主逻辑 =================
def run():
    start_time = time.time()
    bl_data = load_blacklist()
    stats = {"raw": 0, "unique": 0, "bl_skip": 0, "catvod_drop": 0, "ffmpeg_drop": 0, "passed": 0}
    
    # 加载别名
    alias_map = {}
    if os.path.exists(NAME_JSON):
        with open(NAME_JSON, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split(',')
                if len(parts) >= 2: alias_map[parts[0]] = parts[1:]

    # 提取源
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
                    if "catvod.com" in link: stats["catvod_drop"] += 1; continue
                    if is_blacklisted(link, bl_data): stats["bl_skip"] += 1; continue
                    if not link.startswith('http') or link in seen_urls: continue
                    all_channels.append({"name": name, "url": link})
                    seen_urls.add(link)
        except: continue
    
    stats["unique"] = len(all_channels)
    candidate_pool = {}

    # 探测进度条：集成时间、速度、优质源统计
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(deep_analyze_stream, ch['url']): ch for ch in all_channels}
        with tqdm(total=len(all_channels), desc="[正在探测]", unit="ch", bar_format='{l_bar}{bar:20}{r_bar} 优质:{postfix}') as pbar:
            for f in as_completed(futures):
                ch = futures[f]
                h, br = f.result()
                if h > 0:
                    if ch['url'] in bl_data: del bl_data[ch['url']]
                    sid = get_standard_name(ch['name'], alias_map)
                    if sid != "异常源":
                        stats["passed"] += 1
                        ch.update({'h': h, 'sid': sid, 'score': h * 1000 + br/1000})
                        candidate_pool.setdefault(sid, []).append(ch)
                else:
                    stats["ffmpeg_drop"] += 1
                    info = bl_data.get(ch['url'], {"fail_count": 0})
                    info.update({"fail_count": info["fail_count"] + 1, "last_fail": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                    bl_data[ch['url']] = info
                pbar.set_postfix_str(str(stats["passed"]))
                pbar.update(1)

    # 择优保存
    final_list = []
    for sid, sources in candidate_pool.items():
        sources.sort(key=lambda x: x['score'], reverse=True)
        if any(k in sid.upper() for k in ["CCTV", "卫视"]) or sources[0]['h'] >= 720:
            final_list.append(sources[0])

    # 排序与写入
    final_list.sort(key=lambda x: (GROUP_PRIORITY.get("央视频道" if "CCTV" in x['name'] else "综合频道", 99), x['sid']))
    
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            g = "央视频道" if "CCTV" in ch['name'] else ("地方卫视" if "卫视" in ch['name'] else "综合频道")
            f.write(f'#EXTINF:-1 group-title="{g}",{ch["name"]}\n{ch["url"]}\n')

    with open(BLACKLIST_FILE, 'w', encoding='utf-8') as f: json.dump(bl_data, f, indent=2)

    # 修改后的任务摘要报告
    duration = int(time.time() - start_time)
    logger.info(f"""
==================================================
              IPTV 探测任务摘要报告 (本地)
==================================================
1. 初始源梳理:
   - 抓取总数: {stats['raw']}
   - 有效且非 CatVod: {stats['unique']}
   - 屏蔽 CatVod 数量: {stats['catvod_drop']}
   - 黑名单跳过数量: {stats['bl_skip']}

2. 过滤统计:
   - 探测失败(死链/超时): {stats['ffmpeg_drop']}
   - 非核心频道画质未达 720P 已剔除

3. 最终情况:
   - 入选频道总数: {len(final_list)}
   - 央视/卫视完整性: 已执行保底留一策略
   - 任务总耗时: {duration // 60}分{duration % 60}秒
   - 运行日志: crawl.log | 黑名单: {BLACKLIST_FILE}
==================================================
""")

if __name__ == "__main__":
    run()