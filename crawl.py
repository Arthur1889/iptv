import os, platform, subprocess, sys, json, re, time, ssl, warnings, logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 0. 环境与日志设置 =================
warnings.filterwarnings("ignore")
os.environ["WERKZEUG_RUN_MAIN"] = "true" 
ssl._create_default_https_context = ssl._create_unverified_context

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
    config = {"os": sys_type, "ffprobe": "ffprobe", "ffmpeg": "ffmpeg", "timeout": 15, "workers": 50}
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
    if re.match(r'\d{4}-\d{2}-\d{2}', origin_name): return "未知频道"
    name = re.sub(r'\.(cn|hk|tw|us|uk|org)$', '', origin_name.strip(), flags=re.I)
    for main_name, aliases in ALIAS_MAP.items():
        if any(a.strip().upper() in name.upper() for a in aliases): return main_name
    return name

def clean_channel_name(name, height=0, original_name=""):
    noise = r'(HD|高清|超高清|蓝光|频道|\[.*?\]|\(.*?\)|\d+[PpIi]|HEVC|H\.264|H\.265|[-_]\d+$)'
    source_text = original_name if original_name else name
    cleaned = re.sub(noise, '', source_text, flags=re.I).strip().rstrip('- ').strip()
    standard_id = get_standard_name(cleaned)
    display_name = CCTV_DESC.get(standard_id, standard_id)
    is_ultra = height >= 2160 or re.search(r'4K|8K|2160p', source_text, re.I)
    final_display = display_name
    if is_ultra and "4K" not in display_name.upper():
        final_display = f"{display_name}-4K"
    return final_display, standard_id, is_ultra

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
            if any(k in content for k in ["CCTV", "卫视", core, "TV"]): return True, 1.3
            return False, 0.4
    except:
        if os.path.exists(tmp_img): os.remove(tmp_img)
    return False, 1.0

# ================= 3. 主程序逻辑 =================

def run():
    start_time = time.time()
    stats = {"total": 0, "unique": 0, "low_res_discarded": 0, "passed": 0}
    
    logger.info("开始 IPTV 任务 (策略: 央视卫视保全 / 其他 720P 严选)...")
    
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
                    stats["total"] += 1
                    if not link.startswith('http') or link in seen_urls: continue
                    all_channels.append({"name": raw_name, "url": link})
                    seen_urls.add(link)
        except: continue
    
    stats["unique"] = len(all_channels)
    PBAR_FMT = '{l_bar}{bar:20}{r_bar} {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}] 优质源:{postfix}'

    candidate_pool = {}
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(lambda c: (deep_analyze_stream(c['url']), c), ch): ch for ch in all_channels}
        with tqdm(total=len(all_channels), desc="[画质扫描]", bar_format=PBAR_FMT) as pbar:
            for f in as_completed(futures):
                try:
                    (h, br), ch = f.result()
                    f_name, b_id, is_u = clean_channel_name(ch['name'], h, ch['name'])
                    if b_id == "未知频道": continue
                    
                    # 【核心策略修改点】
                    # 判断是否为央视或卫视
                    is_core = any(k in b_id.upper() for k in ["CCTV", "卫视"])
                    
                    # 央视/卫视: 有信号即保留； 其他: 必须 >= 720P
                    if (is_core and h > 0) or (not is_core and h >= 720):
                        stats["passed"] += 1
                        pbar.set_postfix_str(str(stats["passed"]))
                        ch.update({'height': h, 'bitrate': br, 'name': f_name, 'epg_id': b_id, 'is_ultra': is_u, 'phys_score': h * 1000000 + br})
                        if b_id not in candidate_pool: candidate_pool[b_id] = []
                        candidate_pool[b_id].append(ch)
                    else:
                        stats["low_res_discarded"] += 1
                except: pass
                pbar.update(1)

    # 第二级：视觉校验并择优留一
    best_sources = {}; ocr_tasks = []
    for ukey, sources in candidate_pool.items():
        # 排序：物理分数最高（分辨率+码率）排前面
        sources.sort(key=lambda x: x['phys_score'], reverse=True)
        ocr_tasks.extend(sources[:3]) # 仅对该频道前3个优质源进行深度校验

    with ThreadPoolExecutor(max_workers=max(1, ENV["workers"] // 4)) as executor:
        futures = {executor.submit(visual_verify, ch['url'], ch['epg_id']): ch for ch in ocr_tasks 
                   if any(k in ch['epg_id'] for k in ["CCTV", "卫视"])}
        with tqdm(total=len(ocr_tasks), desc="[台标校验]", bar_format=PBAR_FMT) as pbar:
            v_found = 0
            for f in as_completed(futures):
                try:
                    ch = futures[f]; is_real, v_weight = f.result(); ch['phys_score'] *= v_weight
                    ukey = ch['epg_id']
                    # 择优逻辑：如果该 ID 还没被存入，或当前源得分更高，则替换
                    if ukey not in best_sources or ch['phys_score'] > best_sources[ukey]['phys_score']:
                        best_sources[ukey] = ch
                        v_found += 1
                        pbar.set_postfix_str(str(v_found))
                except: pass
                pbar.update(1)
            # 补偿非 OCR 频道 (如影视、港澳台等不跑 OCR 的频道也需要择优留一)
            for ch in ocr_tasks:
                ukey = ch['epg_id']
                if ukey not in best_sources or ch['phys_score'] > best_sources[ukey]['phys_score']:
                    best_sources[ukey] = ch

    # 第五步：排序逻辑 (严格遵循用户需求顺序)
    def sort_logic(x):
        n = x['name'].upper()
        # 分组判定
        g_name = "央视频道" if "CCTV" in n else ("地方卫视" if "卫视" in n else "综合频道")
        if x['is_ultra']: g_name = "4K频道"
        if "山东" in n: g_name = "山东频道"
        if any(k in n for k in ["CHC", "电影", "剧场", "影视"]): g_name = "影视频道"
        if any(k in n for k in ["HBO", "CNN", "NHK", "TVB", "翡翠", "凤凰"]): g_name = "港澳台"
        
        # 央视顺排
        match = re.search(r'CCTV[-]?(\d+)', x['epg_id'], re.I)
        cctv_num = int(match.group(1)) if match else 200
        
        return (GROUP_PRIORITY.get(g_name, 99), cctv_num, -x['phys_score'])

    final_list = sorted(best_sources.values(), key=sort_logic)

    # 第六步：写入 M3U
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            n = ch['name'].upper()
            # 重新判定分组名用于写入
            g_name = "央视频道" if "CCTV" in n else ("地方卫视" if "卫视" in n else "综合频道")
            if ch['is_ultra']: g_name = "4K频道"
            if "山东" in n: g_name = "山东频道"
            if any(k in n for k in ["CHC", "电影", "剧场", "影视"]): g_name = "影视频道"
            if any(k in n for k in ["HBO", "CNN", "NHK", "TVB", "翡翠", "凤凰"]): g_name = "港澳台"
            
            logo_id = ch['epg_id'].replace('-', '').replace(' ', '').replace('+', 'plus')
            f.write(f'#EXTINF:-1 tvg-id="{ch["epg_id"]}" tvg-name="{ch["epg_id"]}" tvg-logo="https://live.fanmingming.com/tv/{logo_id}.png" group-title="{g_name}",{ch["name"]}\n{ch["url"]}\n')

    # 运行摘要
    duration = int(time.time() - start_time)
    logger.info(f"\n================ 任务完成报告 ================\n"
                f"初始总 URL: {stats['total']}\n"
                f"去重后 URL: {stats['unique']}\n"
                f"低画质淘汰 (非核心 < 720P): {stats['low_res_discarded']}\n"
                f"最终精选频道总数: {len(final_list)}\n"
                f"总计运行耗时: {duration//60}分{duration%60}秒\n"
                f"日志记录文件: crawl.log\n"
                f"==============================================")

if __name__ == "__main__":
    run()