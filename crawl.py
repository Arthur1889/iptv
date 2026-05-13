import os
import platform
import subprocess
import sys
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 0. 自动环境准备 =================
def ensure_dependencies():
    """本地运行时自动安装缺失库"""
    required = ["requests", "tqdm", "easyocr"]
    for lib in required:
        try:
            __import__(lib.replace('-', '_'))
        except ImportError:
            print(f"📦 正在安装依赖库: {lib}，请稍候...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", lib])

ensure_dependencies()

import requests
from tqdm import tqdm
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 初始化 OCR 识别引擎
import easyocr
# 首次运行会自动下载模型（约100MB），如果已经下载则直接加载
READER = easyocr.Reader(['ch_sim', 'en'], gpu=False) 

# ================= 1. 配置与环境初始化 =================
CONFIG_FILE = "sources.json"
NAME_JSON = "name.json"

def get_env_config():
    sys_type = platform.system()
    config = {
        "os": sys_type,
        "ffprobe": "ffprobe",
        "ffmpeg": "ffmpeg",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "timeout": 15,
        "workers": 8 # 视觉识别较重，本地建议 8-12
    }
    if sys_type == "Windows" and os.path.exists(r"C:\ffmpeg\bin\ffprobe.exe"):
        config["ffprobe"] = r"C:\ffmpeg\bin\ffprobe.exe"
        config["ffmpeg"] = r"C:\ffmpeg\bin\ffmpeg.exe"
    return config

ENV = get_env_config()

def load_sources():
    if not os.path.exists(CONFIG_FILE): return []
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f).get("urls", [])
    except: return []

def load_alias_map():
    alias_dict = {}
    if not os.path.exists(NAME_JSON): return alias_dict
    try:
        with open(NAME_JSON, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith('#')]
            for line in lines:
                parts = line.split(',')
                if len(parts) < 2: continue
                main_name = parts[0].strip()
                aliases = parts[1:]
                alias_dict[main_name] = aliases
    except: pass
    return alias_dict

SOURCE_URLS = load_sources()
ALIAS_MAP = load_alias_map()
GROUP_PRIORITY = {"央视频道": 1, "地方卫视": 2, "山东频道": 3, "上海频道": 4, "港澳台": 5, "电影/影院": 6}

# ================= 2. 核心逻辑函数 =================

def get_standard_name(origin_name):
    processed_name = re.sub(r'\.(cn|hk|tw|us|uk|org)$', '', origin_name.strip(), flags=re.I)
    name_upper = processed_name.upper()
    for main_name, aliases in ALIAS_MAP.items():
        for alias in aliases:
            alias = alias.strip()
            if alias.startswith("re:"):
                try:
                    if re.search(alias[3:], processed_name, re.I): return main_name
                except: continue
            elif alias.upper() in name_upper or name_upper in alias.upper():
                return main_name
    return processed_name

def clean_channel_name(name, height=0, original_name=""):
    noise = r'(HD|高清|超高清|蓝光|频道|\(备用\)|\(\d+[Pp]\)|\[\d+[Pp]\]|-\d+[Pp]|\d+[Pp])'
    cleaned = re.sub(noise, '', original_name if original_name else name, flags=re.I).strip()
    base_name = get_standard_name(cleaned)
    base_name = re.sub(r'(-4K|-8K|4K|8K|超高清|HD|高清)$', '', base_name, flags=re.I).strip()
    is_ultra = height >= 2160 or re.search(r'4K|8K', original_name, re.I)
    final_name = f"{base_name}-4K" if is_ultra else base_name
    return final_name, base_name, is_ultra

def visual_verify(url, target_name):
    """视觉校验：截取左上角区域并识别台标文字"""
    tmp_img = f"shot_{int(time.time()*1000)}.jpg"
    cmd = [
        ENV["ffmpeg"], "-y", "-t", "3", "-i", url,
        "-vf", "crop=350:180:0:0", "-frames:v", "1", tmp_img
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12)
        if os.path.exists(tmp_img):
            results = READER.readtext(tmp_img, detail=0)
            os.remove(tmp_img)
            content = "".join(results).upper()
            core = re.sub(r'[-频道]', '', target_name).upper()
            if any(k in content for k in ["CCTV", "卫视", core, "TV"]):
                return True, 1.3 
            return False, 0.4 
    except:
        if os.path.exists(tmp_img): os.remove(tmp_img)
    return False, 1.0

def deep_analyze_stream(url):
    cmd = [ENV["ffprobe"], '-v', 'error', '-show_entries', 'format_tags=service_name:stream=width,height,bit_rate', '-of', 'json', '-select_streams', 'v:0', '-timeout', '10000000', url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=ENV["timeout"])
        if result.returncode == 0:
            data = json.loads(result.stdout)
            streams = data.get('streams', [])
            h = int(streams[0].get('height', 0)) if streams else 0
            br = int(streams[0].get('bit_rate', 0)) if streams else 0
            s_name = data.get('format', {}).get('tags', {}).get('service_name', '').upper()
            return h, br, s_name
    except: pass
    return 0, 0, ""

def check_channel(ch):
    h, br, s_name = deep_analyze_stream(ch['url'])
    if any(k in s_name for k in ["SHOPPING", "GO購物", "TEST", "AD", "M3U8"]): return ch, False
    
    if h >= 360:
        f_name, b_name, is_u = clean_channel_name(ch['name'], height=h, original_name=ch['origin_name'])
        v_weight = 1.0
        if any(k in b_name for k in ["CCTV", "卫视", "山东", "齐鲁"]):
            _, v_weight = visual_verify(ch['url'], b_name)
        
        ch['height'], ch['bitrate'] = h, int(br * v_weight)
        ch['name'], ch['epg_id'], ch['is_ultra'] = f_name, b_name, is_u
        return ch, (v_weight >= 0.4)
    return ch, False

def get_group(name):
    n = name.upper()
    if "CCTV" in n: return "央视频道"
    if "卫视" in n: return "地方卫视"
    if any(s in n for s in ["山东", "齐鲁", "济南", "青岛", "潍坊", "烟台", "淄博"]): return "山东频道"
    if any(s in n for s in ["上海", "东方", "新闻综合", "纪实人文"]): return "上海频道"
    k_map = {"港澳台": ["翡翠", "TVB", "凤凰", "明珠", "J2", "HK", "澳门", "台湾"], "电影/影院": ["电影", "影院", "CHC"]}
    for group, keys in k_map.items():
        if any(k in n for k in keys): return group
    return "综合/其他"

def sort_key(ch):
    group_p = GROUP_PRIORITY.get(get_group(ch['name']), 999)
    name = ch['name']
    cctv_m = re.search(r'CCTV-(\d+)', name)
    cctv_n = int(cctv_m.group(1)) if cctv_m else 999
    phys_score = ch.get('height', 0) * 1000000 + ch.get('bitrate', 0)
    return (group_p, cctv_n, ch['epg_id'], -phys_score)

# ================= 3. 主程序 =================

def run():
    if not SOURCE_URLS: return
    all_channels = []
    seen_urls = set()
    session = requests.Session()

    print(f"\n📡 [1/3] 正在从 {len(SOURCE_URLS)} 个源提取链接...")
    for url in SOURCE_URLS:
        try:
            r = session.get(url, timeout=12, verify=False)
            matches = re.findall(r'#EXTINF:.*?(?:tvg-id="(.*?)")?.*?,(.*?)\n(http.*?)(?:\n|$)', r.text)
            for tid, name, link in matches:
                link_clean = link.strip()
                if link_clean not in seen_urls:
                    all_channels.append({"name": tid if tid else name, "origin_name": name, "url": link_clean})
                    seen_urls.add(link_clean)
        except: continue

    print(f"🚀 [2/3] 开启物理+视觉双重校验 (共 {len(all_channels)} 条)...")
    best_sources = {}
    valid_count = 0
    
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(check_channel, ch): ch for ch in all_channels}
        # 强制恢复 tqdm 进度条的所有动态显示
        pbar_fmt = '{l_bar}{bar:20}{r_bar} {n_fmt}/{total_fmt} [{percentage:3.0f}%] 优质源:{postfix}'
        with tqdm(total=len(all_channels), desc="校验进度", bar_format=pbar_fmt) as pbar:
            for f in as_completed(futures):
                res_ch, is_ok = f.result()
                if is_ok:
                    valid_count += 1
                    pbar.set_postfix_str(str(valid_count)) # 实时更新优质源数量
                    
                    unique_key = (res_ch['epg_id'], res_ch['is_ultra'])
                    score = res_ch['height'] * 1000000 + res_ch['bitrate']
                    if unique_key not in best_sources or score > best_sources[unique_key]['phys_score']:
                        res_ch['phys_score'] = score # 修正了排序字段一致性
                        best_sources[unique_key] = res_ch
                pbar.update(1)

    print(f"📦 [3/3] 正在写入 tv.m3u 并排序...")
    final_list = sorted(best_sources.values(), key=sort_key)

    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            logo = ch['epg_id'].replace('-', '')
            f.write(f'#EXTINF:-1 tvg-id="{ch["epg_id"]}" tvg-logo="https://live.fanmingming.com/tv/{logo}.png" group-title="{get_group(ch["name"])}",{ch["name"]}\n{ch["url"]}\n')

    print(f"\n✅ 完成！最终入选 {len(final_list)} 个优质频道。")

if __name__ == "__main__":
    start_time = time.time() # 记录开始时间
    run()
    # 强制在最末尾打印全程耗时
    print(f"⏱️ 全程耗时: {int(time.time() - start_time)} 秒")