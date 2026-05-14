import os, platform, subprocess, sys, json, re, time, ssl, warnings, logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 0. 环境与日志设置 =================
warnings.filterwarnings("ignore")
os.environ["WERKZEUG_RUN_MAIN"] = "true" 
ssl._create_default_https_context = ssl._create_unverified_context

# 配置本地日志文件和控制台输出
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
    required = ["requests", "tqdm", "easyocr"]
    for lib in required:
        try: __import__(lib.replace('-', '_'))
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", lib])

ensure_dependencies()
import requests
from tqdm import tqdm
import easyocr
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 初始化 OCR
READER = easyocr.Reader(['ch_sim', 'en'], gpu=False)

# ================= 1. 配置与硬编码规则 =================
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
    # 本地跑提升并发 workers 数量
    config = {"os": sys_type, "ffprobe": "ffprobe", "ffmpeg": "ffmpeg", "timeout": 12, "workers": 100}
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
    # 纠错：拦截时间戳、短命名或纯 "CCTV"
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

def visual_verify(url, target_name):
    tmp_img = f"shot_{int(time.time()*1000)}.jpg"
    cmd = [ENV["ffmpeg"], "-y", "-t", "3", "-i", url, "-vf", "crop=350:180:0:0", "-frames:v", "1", tmp_img]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        if os.path.exists(tmp_img):
            content = "".join(READER.readtext(tmp_img, detail=0)).upper()
            os.remove(tmp_img)
            core = re.sub(r'[-频道]', '', target_name).upper()
            if any(k in content for k in ["CCTV", "卫视", core, "TV"]): return True, 1.2
            return False, 0.4
    except:
        if os.path.exists(tmp_img): os.remove(tmp_img)
    return False, 1.0

# ================= 3. 主程序 =================

def run():
    start_time = time.time()
    stats = {"raw": 0, "unique": 0, "catvod_drop": 0, "ffmpeg_drop": 0, "ocr_drop": 0, "passed": 0}
    bad_logs, ocr_logs = [], []

    logger.info("任务启动：正在执行本地深度扫描...")
    
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
                    
                    # 5 & 7. 关键词过滤与 catvod 域名硬过滤
                    junk = ["直播室", "轮播", "专题", "课堂", "广播", "购物", "测试"]
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
    # 第一级：FFmpeg 画质探测 (4. 央卫保全 + 非核心 720P)
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(lambda c: (deep_analyze_stream(c['url']), c), ch): ch for ch in all_channels}
        with tqdm(total=len(all_channels), desc="[画质探测]", bar_format=PBAR_FMT) as pbar:
            for f in as_completed(futures):
                try:
                    (h, br), ch = f.result()
                    f_name, sid, is_u = clean_channel_name(ch['name'], h)
                    if sid == "异常源": continue
                    
                    is_core = any(k in sid.upper() for k in ["CCTV", "卫视"])
                    if (is_core and h > 0) or (not is_core and h >= 720):
                        stats["passed"] += 1
                        ch.update({'h': h, 'br': br, 'name': f_name, 'sid': sid, 'is_u': is_u, 'score': h * 1000 + br/1000})
                        if sid not in candidate_pool: candidate_pool[sid] = []
                        candidate_pool[sid].append(ch)
                    else:
                        stats["ffmpeg_drop"] += 1
                        bad_logs.append(f"{ch['name']} - {ch['url']} (画质不足: {h}P)")
                except: stats["ffmpeg_drop"] += 1
                pbar.set_postfix_str(str(stats["passed"]))
                pbar.update(1)

    # 第二级：OCR 择优
    best_sources = {}; ocr_tasks = []
    for sid, sources in candidate_pool.items():
        sources.sort(key=lambda x: x['score'], reverse=True)
        ocr_tasks.extend(sources[:2])

    with ThreadPoolExecutor(max_workers=max(1, ENV["workers"] // 4)) as executor:
        futures = {executor.submit(visual_verify, ch['url'], ch['sid']): ch for ch in ocr_tasks 
                   if any(k in ch['sid'] for k in ["CCTV", "卫视"])}
        with tqdm(total=len(ocr_tasks), desc="[台标校验]", bar_format=PBAR_FMT) as pbar:
            v_ok = 0
            for f in as_completed(futures):
                try:
                    ch = futures[f]; is_ok, weight = f.result()
                    if not is_ok and weight < 1.0: 
                        stats["ocr_drop"] += 1
                        ocr_logs.append(f"{ch['sid']} - {ch['url']} (台标识别失败)")
                    ch['score'] *= weight
                    if ch['sid'] not in best_sources or ch['score'] > best_sources[ch['sid']]['score']:
                        best_sources[ch['sid']] = ch
                        v_ok += 1
                except: pass
                pbar.set_postfix_str(str(v_ok))
                pbar.update(1)
            for ch in ocr_tasks:
                if ch['sid'] not in best_sources: best_sources[ch['sid']] = ch

    # 输出与排序
    def sort_logic(x):
        n = x['name'].upper()
        g = "央视频道" if "CCTV" in n else ("地方卫视" if "卫视" in n else "综合频道")
        if x['is_u']: g = "4K频道"
        if "山东" in n: g = "山东频道"
        match = re.search(r'CCTV[-]?(\d+)', x['sid'], re.I)
        return (GROUP_PRIORITY.get(g, 99), int(match.group(1)) if match else 200, -x['score'])

    final_list = sorted(best_sources.values(), key=sort_logic)

    # 保存文件
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            n = ch['name'].upper()
            g = "央视频道" if "CCTV" in n else ("地方卫视" if "卫视" in n else "综合频道")
            if ch['is_u']: g = "4K频道"
            lid = ch['sid'].replace('-', '').replace(' ', '').replace('+', 'plus')
            f.write(f'#EXTINF:-1 tvg-id="{ch["sid"]}" tvg-name="{ch["sid"]}" tvg-logo="https://live.fanmingming.com/tv/{lid}.png" group-title="{g}",{ch["name"]}\n{ch["url"]}\n')

    with open("filtered_bad_sources.log", "w", encoding="utf-8") as f: f.write("\n".join(bad_logs))
    with open("filtered_ocr_sources.log", "w", encoding="utf-8") as f: f.write("\n".join(ocr_logs))

    # 2. 摘要报告
    duration = int(time.time() - start_time)
    logger.info(f"""
==================================================
              IPTV 探测任务摘要报告
==================================================
1. 初始源梳理:
   - 抓取总 URL 数量: {stats['raw']}
   - 过滤 CatVod 域名: {stats['catvod_drop']}
   - 有效独立 URL 数: {stats['unique']}

2. 过滤情况统计 (重点项):
   - FFmpeg 筛掉 (画质不足或无效): {stats['ffmpeg_drop']}
   - OCR 筛掉 (台标校验不匹配): {stats['ocr_drop']}
   - 注：详细列表见 filtered_*.log

3. 最终情况:
   - 精选成功入选频道: {len(final_list)}
   - 任务总运行耗时: {duration // 60}分{duration % 60}秒
   - 结果文件: tv.m3u | 日志文件: crawl.log
==================================================
""")

if __name__ == "__main__":
    run()