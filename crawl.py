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
import easyocr
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 初始化 OCR (根据本地是否有 GPU 自动切换)
READER = easyocr.Reader(['ch_sim', 'en'], gpu=False) 

# ================= 1. 配置与环境 =================
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
        "workers": 8 # 视觉识别耗资源，建议设为 8-10
    }
    return config

ENV = get_env_config()

# --- 别名映射与分组优先级 ---
def load_alias_map():
    alias_dict = {}
    if os.path.exists(NAME_JSON):
        with open(NAME_JSON, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    parts = line.split(',')
                    if len(parts) >= 2:
                        alias_dict[parts[0].strip()] = parts[1:]
    return alias_dict

ALIAS_MAP = load_alias_map()
GROUP_PRIORITY = {"央视频道": 1, "地方卫视": 2, "山东频道": 3, "港澳台": 5}

# ================= 2. 核心校验逻辑 =================

def get_standard_name(origin_name):
    name = re.sub(r'\.(cn|hk|tw|us|uk|org)$', '', origin_name.strip(), flags=re.I)
    for main_name, aliases in ALIAS_MAP.items():
        if any(a.strip().upper() in name.upper() for a in aliases):
            return main_name
    return name

def clean_channel_name(name, height=0, original_name=""):
    noise = r'(HD|高清|超高清|蓝光|频道|\(备用\)|\(\d+[Pp]\)|\[\d+[Pp]\]|-\d+[Pp]|\d+[Pp])'
    cleaned = re.sub(noise, '', original_name if original_name else name, flags=re.I).strip()
    base_name = get_standard_name(cleaned)
    # 彻底清除 base_name 残留标签
    base_name = re.sub(r'(-4K|-8K|4K|8K|超高清|HD|高清)$', '', base_name, flags=re.I).strip()
    is_ultra = height >= 2160 or re.search(r'4K|8K', original_name, re.I)
    return (f"{base_name}-4K" if is_ultra else base_name), base_name, is_ultra

def visual_verify(url, target_name):
    """截取左上角 350x180 区域进行 OCR"""
    tmp_img = f"shot_{int(time.time()*1000)}.jpg"
    cmd = [ENV["ffmpeg"], "-y", "-t", "3", "-i", url, "-vf", "crop=350:180:0:0", "-frames:v", "1", tmp_img]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12)
        if os.path.exists(tmp_img):
            content = "".join(READER.readtext(tmp_img, detail=0)).upper()
            os.remove(tmp_img)
            core = re.sub(r'[-频道]', '', target_name).upper()
            if any(k in content for k in ["CCTV", "卫视", core, "TV"]):
                return True, 1.3 # 确认为真，加权 30%
            return False, 0.4 # 画面不符，重罚降权
    except:
        if os.path.exists(tmp_img): os.remove(tmp_img)
    return False, 1.0

def deep_analyze_stream(url):
    cmd = [ENV["ffprobe"], '-v', 'error', '-show_entries', 'format_tags=service_name:stream=width,height,bit_rate', '-of', 'json', url]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        data = json.loads(res.stdout)
        s = data['streams'][0]
        s_name = data.get('format', {}).get('tags', {}).get('service_name', '').upper()
        return int(s.get('height', 0)), int(s.get('bit_rate', 0)), s_name
    except: return 0, 0, ""

def check_channel(ch):
    h, br, s_name = deep_analyze_stream(ch['url'])
    if any(k in s_name for k in ["SHOPPING", "AD", "M3U8", "TEST"]): return ch, False
    
    if h >= 360:
        f_name, b_name, is_u = clean_channel_name(ch['name'], h, ch['origin_name'])
        v_weight = 1.0
        # 仅对重点频道开启 OCR 以节省时间
        if any(k in b_name for k in ["CCTV", "卫视", "山东"]):
            _, v_weight = visual_verify(ch['url'], b_name)
            
        ch.update({'height': h, 'bitrate': int(br * v_weight), 'name': f_name, 'epg_id': b_name, 'is_ultra': is_u})
        return ch, v_weight >= 0.4 # 只有视觉极其离谱的才丢弃
    return ch, False

# ================= 3. 主程序与全局去重 =================

def run():
    SOURCE_URLS = json.load(open(CONFIG_FILE, 'r')).get("urls", []) if os.path.exists(CONFIG_FILE) else []
    all_channels = []
    seen_urls = set()
    
    print(f"\n📡 [1/3] 提取链接...")
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, timeout=10, verify=False)
            matches = re.findall(r'#EXTINF:.*?(?:tvg-id="(.*?)")?.*?,(.*?)\n(http.*?)(?:\n|$)', r.text)
            for tid, name, link in matches:
                if link.strip() not in seen_urls:
                    all_channels.append({"name": tid if tid else name, "origin_name": name, "url": link.strip()})
                    seen_urls.add(link.strip())
        except: continue

    print(f"🚀 [2/3] 开启物理+视觉双重校验 (共 {len(all_channels)} 条)...")
    best_sources = {} # 去重核心字典
    valid_count = 0
    
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(check_channel, ch): ch for ch in all_channels}
        pbar_fmt = '{l_bar}{bar:20}{r_bar} {n_fmt}/{total_fmt} [{percentage:3.0f}%] 优质源:{postfix}'
        with tqdm(total=len(all_channels), desc="探测进度", bar_format=pbar_fmt) as pbar:
            for f in as_completed(futures):
                res_ch, is_ok = f.result()
                if is_ok:
                    valid_count += 1
                    pbar.set_postfix_str(str(valid_count))
                    
                    # --- 核心去重 Key: (频道标识, 是否为4K) ---
                    # 这样每个频道的高清版和4K版各只保留一个最优源
                    unique_key = (res_ch['epg_id'], res_ch['is_ultra'])
                    
                    # 综合物理得分 = 分辨率权重 + 视觉加权后的码率
                    phys_score = res_ch['height'] * 1000000 + res_ch['bitrate']
                    
                    if unique_key not in best_sources or phys_score > best_sources[unique_key]['phys_score']:
                        res_ch['phys_score'] = phys_score
                        best_sources[unique_key] = res_ch
                pbar.update(1)

    print(f"📦 [3/3] 正在排序并生成列表...")
    # 排序逻辑：分组优先级 > 物理总得分
    def sort_key(ch):
        def get_group(n):
            if "CCTV" in n.upper(): return "央视频道"
            if "卫视" in n.upper(): return "地方卫视"
            return "综合频道"
        return (GROUP_PRIORITY.get(get_group(ch['name']), 99), -ch['phys_score'])

    final_list = sorted(best_sources.values(), key=sort_key)

    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            f.write(f'#EXTINF:-1 tvg-id="{ch["epg_id"]}" group-title="默认",{ch["name"]}\n{ch["url"]}\n')

    print(f"\n✅ 完成！最终入选 {len(final_list)} 个频道。")

if __name__ == "__main__":
    start = time.time()
    run()
    print(f"⏱️ 全程耗时: {int(time.time() - start)} 秒")