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

# 初始化 OCR (本地建议尝试 gpu=True)
READER = easyocr.Reader(['ch_sim', 'en'], gpu=False)

# ================= 1. 配置与硬编码描述 =================
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
        for p in [r"C:\ffmpeg\bin", r"D:\ffmpeg\bin", r"E:\ffmpeg\bin"]:
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
    if re.match(r'\d{4}-\d{2}-\d{2}', origin_name): return "异常源"
    name = re.sub(r'\.(cn|hk|tw|us|uk|org)$', '', origin_name.strip(), flags=re.I)
    for main_name, aliases in ALIAS_MAP.items():
        if any(a.strip().upper() in name.upper() for a in aliases): return main_name
    return name

def clean_channel_name(name, height=0, original_name=""):
    noise = r'(HD|高清|超高清|蓝光|频道|\[.*?\]|\(.*?\)|\d+[PpIi]|HEVC|H\.264|H\.265|[-_]\d+$)'
    source_text = original_name if original_name else name
    cleaned = re.sub(noise, '', source_text, flags=re.I).strip().rstrip('- ').strip()
    sid = get_standard_name(cleaned)
    display = CCTV_DESC.get(sid, sid)
    is_ultra = height >= 2160 or re.search(r'4K|8K|2160p', source_text, re.I)
    final = f"{display}-4K" if is_ultra and "4K" not in display.upper() else display
    return final, sid, is_ultra

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
            return False, 0.5
    except:
        if os.path.exists(tmp_img): os.remove(tmp_img)
    return False, 1.0

# ================= 3. 主逻辑 =================

def run():
    start_time = time.time()
    stats = {"raw": 0, "unique": 0, "ffmpeg_fail": 0, "ocr_fail": 0, "passed": 0}
    
    logger.info("本地任务启动：央视卫视保全 + 720P严选逻辑")
    
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
                    # 5. 删除直播室/轮播相关
                    if any(k in raw_name for k in ["直播室", "轮播", "专题", "课堂"]): continue
                    if not link.startswith('http') or link in seen_urls: continue
                    all_channels.append({"name": raw_name, "url": link})
                    seen_urls.add(link)
        except: continue
    
    stats["unique"] = len(all_channels)
    PBAR_FMT = '{l_bar}{bar:25}{r_bar} {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}] 优质:{postfix}'

    candidate_pool = {}
    # 4. FFmpeg 探测 (画质筛选)
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(lambda c: (deep_analyze_stream(c['url']), c), ch): ch for ch in all_channels}
        with tqdm(total=len(all_channels), desc="[FFmpeg 扫描]", bar_format=PBAR_FMT) as pbar:
            for f in as_completed(futures):
                try:
                    (h, br), ch = f.result()
                    f_name, sid, is_u = clean_channel_name(ch['name'], h, ch['name'])
                    if sid == "异常源": continue
                    
                    is_core = any(k in sid.upper() for k in ["CCTV", "卫视"])
                    # 策略：央卫保全(h>0)；其他(h>=720)
                    if (is_core and h > 0) or (not is_core and h >= 720):
                        stats["passed"] += 1
                        pbar.set_postfix_str(str(stats["passed"]))
                        ch.update({'height': h, 'br': br, 'name': f_name, 'sid': sid, 'is_u': is_u, 'score': h * 1000 + br/1000})
                        if sid not in candidate_pool: candidate_pool[sid] = []
                        candidate_pool[sid].append(ch)
                    else:
                        stats["ffmpeg_fail"] += 1
                except: stats["ffmpeg_fail"] += 1
                pbar.update(1)

    # 4. OCR 验证 (台标去伪)
    best_sources = {}; ocr_tasks = []
    for sid, sources in candidate_pool.items():
        sources.sort(key=lambda x: x['score'], reverse=True)
        ocr_tasks.extend(sources[:2]) # 每个频道选前2个最好的跑OCR

    with ThreadPoolExecutor(max_workers=max(1, ENV["workers"] // 4)) as executor:
        futures = {executor.submit(visual_verify, ch['url'], ch['sid']): ch for ch in ocr_tasks 
                   if any(k in ch['sid'] for k in ["CCTV", "卫视"])}
        with tqdm(total=len(ocr_tasks), desc="[OCR 校验  ]", bar_format=PBAR_FMT) as pbar:
            v_ok = 0
            for f in as_completed(futures):
                try:
                    ch = futures[f]; is_real, weight = f.result()
                    if weight < 1.0: stats["ocr_fail"] += 1
                    ch['score'] *= weight
                    ukey = ch['sid']
                    if ukey not in best_sources or ch['score'] > best_sources[ukey]['score']:
                        best_sources[ukey] = ch
                        v_ok += 1; pbar.set_postfix_str(str(v_ok))
                except: pass
                pbar.update(1)
            for ch in ocr_tasks: # 补偿非央卫频道择优
                if ch['sid'] not in best_sources or ch['score'] > best_sources[ch['sid']]['score']:
                    best_sources[ch['sid']] = ch

    # 排序与输出
    def sort_key(x):
        n = x['name'].upper()
        g = "央视频道" if "CCTV" in n else ("地方卫视" if "卫视" in n else "综合频道")
        if x['is_u']: g = "4K频道"
        if "山东" in n: g = "山东频道"
        match = re.search(r'CCTV[-]?(\d+)', x['sid'], re.I)
        return (GROUP_PRIORITY.get(g, 99), int(match.group(1)) if match else 200, -x['score'])

    final_list = sorted(best_sources.values(), key=sort_key)

    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            n = ch['name'].upper()
            g = "央视频道" if "CCTV" in n else ("地方卫视" if "卫视" in n else "综合频道")
            if ch['is_u']: g = "4K频道"
            if "山东" in n: g = "山东频道"
            if any(k in n for k in ["CHC", "电影", "影视"]): g = "影视频道"
            lid = ch['sid'].replace('-', '').replace(' ', '').replace('+', 'plus')
            f.write(f'#EXTINF:-1 tvg-id="{ch["sid"]}" tvg-name="{ch["sid"]}" tvg-logo="https://live.fanmingming.com/tv/{lid}.png" group-title="{g}",{ch["name"]}\n{ch["url"]}\n')

    # 2 & 6. 最终摘要报告
    duration = int(time.time() - start_time)
    summary = f"""
==================================================
              IPTV 探测任务摘要报告 (本地)
==================================================
1. 初始源梳理:
   - 抓取原始 URL 总数: {stats['raw']}
   - 移除重复/直播室/轮播后: {stats['unique']}

2. 过滤情况统计:
   - FFmpeg 筛掉 (画质<720P 或连通失败): {stats['ffmpeg_fail']}
   - OCR 筛掉 (台标不符或校验失败): {stats['ocr_fail']}
   - 央视/卫视完整性策略: 已确保所有有信号频道保留最佳源

3. 最终情况:
   - 成功入选频道总数: {len(final_list)}
   - 任务总耗时: {duration // 60}分{duration % 60}秒
   - 结果文件: tv.m3u
   - 日志文件: crawl.log
==================================================
"""
    logger.info(summary)

if __name__ == "__main__":
    run()