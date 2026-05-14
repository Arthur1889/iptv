import os, platform, subprocess, sys, json, re, time, ssl, warnings, logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 0. 环境与日志设置 =================
warnings.filterwarnings("ignore")
os.environ["WERKZEUG_RUN_MAIN"] = "true" 
ssl._create_default_https_context = ssl._create_unverified_context

# 配置本地日志
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

# 初始化 OCR (本地有显卡建议设 gpu=True)
READER = easyocr.Reader(['ch_sim', 'en'], gpu=False)

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
    # 本地跑提升 workers 数量
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

# ================= 2. 核心逻辑函数 =================

def get_standard_name(origin_name):
    name_str = origin_name.strip()
    # 1. 过滤异常源和单纯叫 "CCTV" 的不规范名
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
            return False, 0.4 # OCR 识别不匹配，大幅降分
    except:
        if os.path.exists(tmp_img): os.remove(tmp_img)
    return False, 1.0

# ================= 3. 主程序 =================

def run():
    start_time = time.time()
    # 统计数据
    stats = {"raw": 0, "unique": 0, "ffmpeg_drop": 0, "ocr_drop": 0, "passed": 0}
    # 过滤记录列表
    ffmpeg_filtered_log = []
    ocr_filtered_log = []

    logger.info("本地任务启动：正在读取源文件...")
    
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
                    
                    # 直播室/垃圾源前置清理
                    junk = ["直播室", "轮播", "专题", "课堂", "广播", "购物", "测试"]
                    if any(k in raw_name for k in junk) or raw_name.upper() == "CCTV": continue
                    if not link.startswith('http') or link in seen_urls: continue
                    
                    all_channels.append({"name": raw_name, "url": link})
                    seen_urls.add(link)
        except: continue
    
    stats["unique"] = len(all_channels)
    PBAR_FMT = '{l_bar}{bar:20}{r_bar} {n_fmt}/{total_fmt} [{elapsed}<{remaining}] 优质:{postfix}'

    candidate_pool = {}
    # 第一阶段：FFmpeg 画质探测
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(lambda c: (deep_analyze_stream(c['url']), c), ch): ch for ch in all_channels}
        with tqdm(total=len(all_channels), desc="[1. 画质探测]", bar_format=PBAR_FMT) as pbar:
            for f in as_completed(futures):
                try:
                    (h, br), ch = f.result()
                    f_name, sid, is_u = clean_channel_name(ch['name'], h)
                    
                    is_core = any(k in sid.upper() for k in ["CCTV", "卫视"])
                    # 央卫保全 + 非核心 720P 逻辑
                    if (is_core and h > 0) or (not is_core and h >= 720):
                        stats["passed"] += 1
                        ch.update({'height': h, 'br': br, 'name': f_name, 'sid': sid, 'is_u': is_u, 'score': h * 1000 + br/1000})
                        if sid not in candidate_pool: candidate_pool[sid] = []
                        candidate_pool[sid].append(ch)
                    else:
                        stats["ffmpeg_drop"] += 1
                        ffmpeg_filtered_log.append(f"{ch['name']}, {ch['url']} (画质:{h}P)")
                except: 
                    stats["ffmpeg_drop"] += 1
                pbar.set_postfix_str(str(stats["passed"]))
                pbar.update(1)

    # 第二阶段：OCR 择优
    best_sources = {}; ocr_tasks = []
    for sid, sources in candidate_pool.items():
        sources.sort(key=lambda x: x['score'], reverse=True)
        ocr_tasks.extend(sources[:2]) # 每个频道挑前2个跑OCR

    with ThreadPoolExecutor(max_workers=max(1, ENV["workers"] // 4)) as executor:
        futures = {executor.submit(visual_verify, ch['url'], ch['sid']): ch for ch in ocr_tasks 
                   if any(k in ch['sid'] for k in ["CCTV", "卫视"])}
        with tqdm(total=len(ocr_tasks), desc="[2. 台标校验]", bar_format=PBAR_FMT) as pbar:
            for f in as_completed(futures):
                try:
                    ch = futures[f]; is_ok, weight = f.result()
                    if not is_ok and weight < 1.0: 
                        stats["ocr_drop"] += 1
                        ocr_filtered_log.append(f"{ch['name']}, {ch['url']} (台标校验未通过)")
                    ch['score'] *= weight
                    ukey = ch['sid']
                    if ukey not in best_sources or ch['score'] > best_sources[ukey]['score']:
                        best_sources[ukey] = ch
                except: pass
                pbar.update(1)
            # 补偿非 OCR 频道
            for ch in ocr_tasks:
                if ch['sid'] not in best_sources: best_sources[ch['sid']] = ch

    # 第三阶段：排序与文件保存
    def sort_logic(x):
        n = x['name'].upper()
        g = "央视频道" if "CCTV" in n else ("地方卫视" if "卫视" in n else "综合频道")
        if x['is_u']: g = "4K频道"
        if "山东" in n: g = "山东频道"
        match = re.search(r'CCTV[-]?(\d+)', x['sid'], re.I)
        return (GROUP_PRIORITY.get(g, 99), int(match.group(1)) if match else 200, -x['score'])

    final_list = sorted(best_sources.values(), key=sort_logic)

    # 1. 保存 tv.m3u
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            n = ch['name'].upper()
            g = "央视频道" if "CCTV" in n else ("地方卫视" if "卫视" in n else "综合频道")
            if ch['is_u']: g = "4K频道"
            lid = ch['sid'].replace('-', '').replace(' ', '').replace('+', 'plus')
            f.write(f'#EXTINF:-1 tvg-id="{ch["sid"]}" tvg-name="{ch["sid"]}" tvg-logo="https://live.fanmingming.com/tv/{lid}.png" group-title="{g}",{ch["name"]}\n{ch["url"]}\n')

    # 2. 保存过滤后的源到文件
    with open("filtered_bad_sources.log", "w", encoding="utf-8") as f:
        f.write("\n".join(ffmpeg_filtered_log))
    with open("filtered_ocr_sources.log", "w", encoding="utf-8") as f:
        f.write("\n".join(ocr_filtered_log))

    # 4. 打印摘要
    duration = int(time.time() - start_time)
    logger.info(f"""
================ 任务摘要报告 ================
1. 初始源梳理:
   - 总 URL 提取数: {stats['raw']}
   - 有效去重数: {stats['unique']}

2. 过滤详情:
   - FFmpeg 过滤 (画质/无效): {stats['ffmpeg_drop']} (详见 filtered_bad_sources.log)
   - OCR 过滤 (台标伪造): {stats['ocr_drop']} (详见 filtered_ocr_sources.log)

3. 最终情况:
   - 精选入选频道: {len(final_list)}
   - 任务耗时: {duration // 60}分{duration % 60}秒
==============================================""")

if __name__ == "__main__":
    run()