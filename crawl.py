import os
import platform
import subprocess
import sys
import json
import re
import time
import ssl
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 0. 环境与干扰屏蔽 =================
warnings.filterwarnings("ignore")
os.environ["WERKZEUG_RUN_MAIN"] = "true" 
ssl._create_default_https_context = ssl._create_unverified_context

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

# ================= 1. 配置逻辑 =================
CONFIG_FILE = "sources.json"
NAME_JSON = "name.json"

GROUP_PRIORITY = {
    "4K频道": 1, "央视频道": 2, "地方卫视": 3, "山东频道": 4, 
    "港澳台": 5, "数字频道": 6, "综合频道": 10
}

def get_env_config():
    sys_type = platform.system()
    # 【修改点】第一级探测线程改为 50
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

# ================= 2. 核心清洗与工具 =================

def get_standard_name(origin_name):
    name = re.sub(r'\.(cn|hk|tw|us|uk|org)$', '', origin_name.strip(), flags=re.I)
    for main_name, aliases in ALIAS_MAP.items():
        if any(a.strip().upper() in name.upper() for a in aliases): return main_name
    return name

def clean_channel_name(name, height=0, original_name=""):
    noise = r'(HD|高清|超高清|蓝光|频道|\[.*?\]|\(.*?\)|\d+[PpIi]|Geo-blocked|Not 24/7|HEVC|H\.264|H\.265|\(备用\)|\d+fps|[-_]\d+$)'
    source_text = original_name if original_name else name
    cleaned = re.sub(noise, '', source_text, flags=re.I).strip().rstrip('- ').strip()
    full_display_name = get_standard_name(cleaned)
    pure_id = full_display_name.split(' ')[0] if ' ' in full_display_name else full_display_name
    is_ultra = height >= 2160 or re.search(r'4K|8K|2160p', source_text, re.I)
    return (f"{full_display_name}-4K" if is_ultra else full_display_name), pure_id, is_ultra

def deep_analyze_stream(url):
    cmd = [ENV["ffprobe"], '-v', 'error', '-show_entries', 'format_tags=service_name:stream=width,height,bit_rate', '-of', 'json', '-select_streams', 'v:0', url]
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

# ================= 3. 任务执行逻辑 =================

def check_quality(ch):
    h, br = deep_analyze_stream(ch['url'])
    if h >= 360:
        f_name, b_id, is_u = clean_channel_name(ch['name'], h, ch['origin_name'])
        ch.update({'height': h, 'bitrate': br, 'name': f_name, 'epg_id': b_id, 'is_ultra': is_u, 'phys_score': h * 1000000 + br})
        return ch, True
    return ch, False

def get_cctv_rank(name):
    if "CCTV" not in name.upper(): return 999
    num_match = re.search(r'CCTV[-]?(\d+)', name, re.I)
    return int(num_match.group(1)) if num_match else 200

def run():
    if not os.path.exists(CONFIG_FILE): return
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        SOURCE_URLS = json.load(f).get("urls", [])

    all_channels = []
    seen_urls = set()
    
    print(f"\n📡 [1/3] 提取链接...")
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, timeout=10, verify=False)
            lines = r.text.split('\n')
            for i in range(len(lines)):
                if lines[i].startswith('#EXTINF:'):
                    raw_name = lines[i].split(',')[-1].strip()
                    link = lines[i+1].strip() if i+1 < len(lines) else ""
                    if not link.startswith('http') or link in seen_urls: continue
                    if any(k in raw_name for k in ["直播室", "影院", "轮播", "专题"]): continue
                    all_channels.append({"name": raw_name, "origin_name": raw_name, "url": link})
                    seen_urls.add(link)
        except: continue

    # 通用进度条格式
    PBAR_FMT = '{l_bar}{bar:20}{r_bar} {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}] 优质源:{postfix}'

    print(f"🚀 [2/3] 第一级：50 线程画质筛选...")
    candidate_pool = {}
    quality_ok_count = 0
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(check_quality, ch): ch for ch in all_channels}
        with tqdm(total=len(all_channels), desc="画质扫描", bar_format=PBAR_FMT) as pbar:
            for f in as_completed(futures):
                res_ch, is_ok = f.result()
                if is_ok:
                    quality_ok_count += 1
                    pbar.set_postfix_str(str(quality_ok_count))
                    ukey = res_ch['epg_id']
                    if ukey not in candidate_pool: candidate_pool[ukey] = []
                    candidate_pool[ukey].append(res_ch)
                pbar.update(1)

    print(f"👁️ [新环节] 第二级：OCR 视觉精选 (保守线程)...")
    best_sources = {}
    ocr_tasks = []
    for ukey, sources in candidate_pool.items():
        sources.sort(key=lambda x: x['phys_score'], reverse=True)
        ocr_tasks.extend(sources[:3])  # 每个频道仅验证前 3 名

    verified_count = 0
    # OCR 设为 workers // 4，即 12 线程左右，防止 CPU 爆炸
    with ThreadPoolExecutor(max_workers=max(1, ENV["workers"] // 4)) as executor:
        # 仅对央视/卫视进行视觉验证
        futures = {executor.submit(visual_verify, ch['url'], ch['epg_id']): ch for ch in ocr_tasks 
                   if any(k in ch['epg_id'] for k in ["CCTV", "卫视"])}
        
        with tqdm(total=len(ocr_tasks), desc="OCR 验证", bar_format=PBAR_FMT) as pbar:
            for f in as_completed(futures):
                ch = futures[f]
                is_real, v_weight = f.result()
                ch['phys_score'] *= v_weight
                ukey = ch['epg_id']
                if ukey not in best_sources or ch['phys_score'] > best_sources[ukey]['phys_score']:
                    if ukey not in best_sources: verified_count += 1
                    best_sources[ukey] = ch
                pbar.set_postfix_str(str(verified_count))
                pbar.update(1)
            
            # 处理无需 OCR 的频道
            for ch in ocr_tasks:
                if ch['epg_id'] not in best_sources:
                    verified_count += 1
                    best_sources[ch['epg_id']] = ch
                    pbar.set_postfix_str(str(verified_count))
                pbar.update(1)

    print(f"📦 [3/3] 生成有序列表...")
    final_list = sorted(best_sources.values(), key=lambda x: (
        GROUP_PRIORITY.get("央视频道" if "CCTV" in x['name'].upper() else ("地方卫视" if "卫视" in x['name'] else "综合频道"), 99),
        get_cctv_rank(x['name']),
        -x['phys_score']
    ))

    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            g_name = "央视频道" if "CCTV" in ch['name'].upper() else ("地方卫视" if "卫视" in ch['name'] else "综合频道")
            logo_id = ch['epg_id'].replace('-', '').replace(' ', '')
            f.write(f'#EXTINF:-1 tvg-id="{ch["epg_id"]}" tvg-logo="https://live.fanmingming.com/tv/{logo_id}.png" group-title="{g_name}",{ch["name"]}\n{ch["url"]}\n')

    print(f"\n✅ 完成！最终入选 {len(best_sources)} 个频道。")

if __name__ == "__main__":
    start_time = time.time()
    run()
    duration = int(time.time() - start_time)
    print(f"⏱️ 总耗时: {duration // 60}分{duration % 60}秒")