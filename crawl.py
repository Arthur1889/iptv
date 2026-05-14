import os, platform, subprocess, sys, json, re, time, ssl, warnings, logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 0. 环境与日志设置 =================
warnings.filterwarnings("ignore")
os.environ["WERKZEUG_RUN_MAIN"] = "true" 
ssl._create_default_https_context = ssl._create_unverified_context

# 配置本地 crawl.log
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
    # 移除 easyocr，仅保留基础库
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

# ================= 1. 配置与规则 =================
CONFIG_FILE = "sources.json"
NAME_JSON = "name.json"

CCTV_DESC = {
    "CCTV-1": "CCTV-1 综合", "CCTV-2": "CCTV-2 财经", "CCTV-3": "CCTV-3 综艺",
    "CCTV-4": "CCTV-4 中文国际", "CCTV-5": "CCTV-5 体育", "CCTV-5+": "CCTV-5+ 体育赛事",
    "CCTV-6": "CCTV-6 电影", "CCTV-7": "CCTV-7 国防军事", "CCTV-8": "CCTV-8 电视剧",
    "CCTV-9": "CCTV-9 纪录", "CCTV-10": "CCTV-10 科教", "CCTV-11": "CCTV-11 戏曲",
    "CCTV-12": "CCTV-12 社会与法", "CCTV-13": "CCTV-13 新闻", "CCTV-14": "CCTV-14 少儿",
    "CCTV-15": "CCTV-15 音乐", "CCTV-16": "CCTV-16 奥林匹克", "CCTV-17": "CCTV-17 农业农村"
}

GROUP_PRIORITY = {
    "4K频道": 1, "央视频道": 2, "地方卫视": 3, "山东频道": 4,
    "数字频道": 5, "影视频道": 6, "港澳台": 7, "综合频道": 10
}

def get_env_config():
    sys_type = platform.system()
    config = {"os": sys_type, "ffprobe": "ffprobe", "ffmpeg": "ffmpeg", "timeout": 12, "workers": 120}
    if sys_type == "Windows":
        for p in [r"C:\ffmpeg\bin", r"D:\ffmpeg\bin"]:
            if os.path.exists(os.path.join(p, "ffmpeg.exe")):
                config["ffmpeg"] = os.path.join(p, "ffmpeg.exe")
                config["ffprobe"] = os.path.join(p, "ffprobe.exe")
                break
    return config

ENV = get_env_config()

def load_alias_map():
    alias_dict = {}
    if os.path.exists(NAME_JSON):
        with open(NAME_JSON, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    parts = line.split(',')
                    if len(parts) >= 2: alias_dict[parts[0].strip()] = parts[1:]
    return alias_dict

ALIAS_MAP = load_alias_map()

# ================= 2. 核心功能函数 =================

def get_standard_name(origin_name):
    name_str = origin_name.strip()
    if re.match(r'\d{4}-\d{2}-\d{2}', name_str) or name_str.upper() == "CCTV" or len(name_str) < 2:
        return "异常源"
    name = re.sub(r'\.(cn|hk|tw|us|uk|org)$', '', name_str, flags=re.I)
    for main_name, aliases in ALIAS_MAP.items():
        if any(a.strip().upper() in name.upper() for a in aliases): return main_name
    return name

def clean_channel_name(name, height=0):
    noise = r'(HD|高清|超高清|蓝光|频道|\[.*?\]|\(.*?\)|\d+[PpIi]|HEVC|H\.264|H\.265|[-_]\d+$)'
    cleaned = re.sub(noise, '', name, flags=re.I).strip().rstrip('- ').strip()
    sid = get_standard_name(cleaned)
    display = CCTV_DESC.get(sid, sid)
    is_u = height >= 2160
    final = f"{display}-4K" if is_u and "4K" not in display.upper() else display
    return final, sid, is_u

def deep_analyze_stream(url):
    cmd = [ENV["ffprobe"], '-v', 'error', '-show_entries', 'stream=width,height,bit_rate', '-of', 'json', '-select_streams', 'v:0', url]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=ENV["timeout"], errors='ignore')
        data = json.loads(res.stdout)
        s = data['streams'][0]
        return int(s.get('height', 0)), int(s.get('bit_rate', 0))
    except: return 0, 0

# ================= 3. 主程序 =================

def run():
    start_time = time.time()
    stats = {"raw": 0, "unique": 0, "catvod_drop": 0, "ffmpeg_drop": 0, "passed": 0}
    
    logger.info("任务启动：正在读取源文件...")
    
    if not os.path.exists(CONFIG_FILE): return
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        SOURCE_URLS = json.load(f).get("urls", [])

    all_channels = []; seen_urls = set()
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, timeout=10, verify=False)
            lines = r.text.split('\n')
            for i in range(len(lines)):
                if lines[i].startswith('#EXTINF:'):
                    raw_name = lines[i].split(',')[-1].strip()
                    link = lines[i+1].strip() if i+1 < len(lines) else ""
                    stats["raw"] += 1
                    
                    # 5 & 6. 关键词过滤与 catvod 过滤
                    junk = ["直播室", "轮播", "专题", "课堂", "广播", "购物", "测试", "电影", "剧集"]
                    if any(k in raw_name for k in junk) or raw_name.upper() == "CCTV": continue
                    if "catvod.com" in link: 
                        stats["catvod_drop"] += 1
                        continue
                    if not link.startswith('http') or link in seen_urls: continue
                    
                    all_channels.append({"name": raw_name, "url": link})
                    seen_urls.add(link)
        except: continue
    
    stats["unique"] = len(all_channels)
    PBAR_FMT = '{l_bar}{bar:25}{r_bar} {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}] 优质:{postfix}'

    candidate_pool = {}
    # FFmpeg 探测
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(lambda c: (deep_analyze_stream(c['url']), c), ch): ch for ch in all_channels}
        with tqdm(total=len(all_channels), desc="[正在探测]", bar_format=PBAR_FMT) as pbar:
            for f in as_completed(futures):
                try:
                    (h, br), ch = f.result()
                    f_name, sid, is_u = clean_channel_name(ch['name'], h)
                    if sid == "异常源": continue
                    
                    is_core = any(k in sid.upper() for k in ["CCTV", "卫视"])
                    
                    # 4. 择优逻辑：只要有信号(h>0)就先入围
                    if h > 0:
                        stats["passed"] += 1
                        ch.update({'h': h, 'br': br, 'name': f_name, 'sid': sid, 'is_u': is_u, 'score': h * 1000 + br/1000})
                        if sid not in candidate_pool: candidate_pool[sid] = []
                        candidate_pool[sid].append(ch)
                    else:
                        stats["ffmpeg_drop"] += 1
                except: stats["ffmpeg_drop"] += 1
                pbar.set_postfix_str(str(stats["passed"]))
                pbar.update(1)

    # 4. 最终过滤与去重 (确保 720P 以上，但保底留一)
    best_sources = {}
    for sid, sources in candidate_pool.items():
        sources.sort(key=lambda x: x['score'], reverse=True)
        is_core = any(k in sid.upper() for k in ["CCTV", "卫视"])
        
        # 挑选最佳源
        best_one = sources[0]
        # 如果是核心频道，或者质量 >= 720P，则保留
        if is_core or best_one['h'] >= 720:
            best_sources[sid] = best_one
        else:
            stats["ffmpeg_drop"] += 1 # 如果非核心且没达到720P，计入过滤

    # 排序逻辑
    def sort_logic(x):
        n = x['name'].upper()
        g = "央视频道" if "CCTV" in n else ("地方卫视" if "卫视" in n else "综合频道")
        if x['is_u']: g = "4K频道"
        if "山东" in n: g = "山东频道"
        match = re.search(r'CCTV[-]?(\d+)', x['sid'], re.I)
        return (GROUP_PRIORITY.get(g, 99), int(match.group(1)) if match else 200, -x['score'])

    final_list = sorted(best_sources.values(), key=sort_logic)

    # 保存 tv.m3u
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            n = ch['name'].upper()
            g = "央视频道" if "CCTV" in n else ("地方卫视" if "卫视" in n else "综合频道")
            if ch['is_u']: g = "4K频道"
            lid = ch['sid'].replace('-', '').replace(' ', '').replace('+', 'plus')
            f.write(f'#EXTINF:-1 tvg-id="{ch["sid"]}" tvg-name="{ch["sid"]}" tvg-logo="https://live.fanmingming.com/tv/{lid}.png" group-title="{g}",{ch["name"]}\n{ch["url"]}\n')

    # 2. 摘要
    duration = int(time.time() - start_time)
    logger.info(f"""
==================================================
              IPTV 探测任务摘要报告 (本地)
==================================================
1. 初始源梳理:
   - 抓取总数: {stats['raw']}
   - 有效且非 CatVod: {stats['unique']}
   - 屏蔽 CatVod 数量: {stats['catvod_drop']}

2. 过滤统计:
   - 无信号或非高清过滤: {stats['ffmpeg_drop']}
   - 注：OCR 校验已按需关闭

3. 最终情况:
   - 入选频道总数: {len(final_list)}
   - 央视/卫视完整性: 已针对核心频道开启保底留一策略
   - 任务耗时: {duration // 60}分{duration % 60}秒
   - 运行日志: crawl.log
==================================================
""")

if __name__ == "__main__":
    run()