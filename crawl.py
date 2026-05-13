import os
import platform
import subprocess
import sys
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 0. 环境准备 =================
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def ensure_dependencies():
    required_libs = ["requests", "tqdm"]
    for lib in required_libs:
        try:
            __import__(lib)
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", lib])

ensure_dependencies()
import requests
from tqdm import tqdm

def get_env_config():
    sys_type = platform.system()
    config = {
        "os": sys_type, 
        "ffprobe": "ffprobe", 
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", 
        "timeout": 15, 
        "workers": 50 
    }
    if sys_type == "Windows" and os.path.exists(r"C:\ffmpeg\bin\ffprobe.exe"):
        config["ffprobe"] = r"C:\ffmpeg\bin\ffprobe.exe"
    return config

ENV = get_env_config()

# ================= 1. 加载配置与别名表 =================
CONFIG_FILE = "sources.json"
NAME_JSON = "name.json"

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
    except Exception as e:
        print(f"⚠️ 加载 name.json 失败: {e}")
    return alias_dict

SOURCE_URLS = load_sources()
ALIAS_MAP = load_alias_map()

# 分组优先级
GROUP_PRIORITY = {"央视频道": 1, "地方卫视": 2, "山东频道": 3, "上海频道": 4, "港澳台": 5, "电影/影院": 6, "体育/竞技": 7, "英文/国际": 8, "纪录/纪实": 9, "少儿/动画": 10}

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
    noise_pattern = r'(HD|高清|超高清|蓝光|频道|\(备用\)|\(\d+[Pp]\)|\[\d+[Pp]\]|-\d+[Pp]|\d+[Pp])'
    cleaned_origin = re.sub(noise_pattern, '', original_name if original_name else name, flags=re.I).strip()
    base_name = get_standard_name(cleaned_origin)
    base_name = re.sub(r'(-4K|-8K|4K|8K|超高清|HD|高清)$', '', base_name, flags=re.I).strip()
    
    is_8k = height >= 4320 or re.search(r'8K', original_name, re.I)
    is_4k = height >= 2160 or re.search(r'4K', original_name, re.I)

    is_ultra = False
    if is_8k: 
        final_name = f"{base_name}-8K"; is_ultra = True
    elif is_4k: 
        final_name = f"{base_name}-4K"; is_ultra = True
    else:
        final_name = base_name; is_ultra = False
        
    return final_name, base_name, is_ultra

def deep_analyze_stream(url):
    """
    深度探测：获取分辨率、码率及 Service Name (元数据)
    """
    cmd = [
        ENV["ffprobe"], '-v', 'error', 
        '-show_entries', 'format_tags=service_name:stream=width,height,bit_rate', 
        '-of', 'json', '-select_streams', 'v:0', '-timeout', '10000000', url
    ]
    try:
        cf = subprocess.CREATE_NO_WINDOW if ENV["os"] == "Windows" else 0
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=ENV["timeout"], creationflags=cf)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            streams = data.get('streams', [])
            h = int(streams[0].get('height', 0)) if streams else 0
            br = int(streams[0].get('bit_rate', 0)) if streams else 0
            tags = data.get('format', {}).get('tags', {})
            s_name = tags.get('service_name', '').upper()
            return h, br, s_name
    except: pass
    return 0, 0, ""

def check_channel(ch):
    """
    筛选逻辑：元数据校验 + 码率底线 + 来源权重惩罚
    """
    # 假源常见关键词黑名单
    BAD_SERVICE_KEYWORDS = ["SHOPPING", "GO購物", "TEST", "DEMO", "AD", "광고", "彩条", "M3U8"]
    
    try:
        h, br, s_name = deep_analyze_stream(ch['url'])
        
        # 1. 元数据黑名单过滤
        if any(k in s_name for k in BAD_SERVICE_KEYWORDS):
            return ch, False

        # 2. 码率红线过滤 (720P以上低于800k基本判定为假源或画质极差)
        if h >= 720 and br > 0 and br < 800000:
            return ch, False

        if h >= 360:
            ch['height'], ch['bitrate'] = h, br
            ch['name'], ch['epg_id'], ch['is_ultra'] = clean_channel_name(ch['name'], height=h, original_name=ch['origin_name'])
            
            # 3. 来源一致性惩罚：如果流内名称与匹配名完全不搭，降权 20%
            if s_name and ch['epg_id'].upper() not in s_name:
                ch['bitrate'] = int(ch['bitrate'] * 0.8)

            return ch, True
    except: pass
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
    cctv_match = re.search(r'CCTV-(\d+)', name)
    cctv_num = int(cctv_match.group(1)) if cctv_match else 999
    quality_rank = 20 if "-8K" in name else (10 if "-4K" in name else 0)
    phys_score = ch.get('height', 0) * 1000000 + ch.get('bitrate', 0)
    return (group_p, cctv_num, ch['epg_id'], -quality_rank, -phys_score)

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
                link = link.strip()
                if link not in seen_urls:
                    all_channels.append({"name": tid if tid else name, "origin_name": name, "url": link})
                    seen_urls.add(link)
        except: continue

    print(f"🚀 [2/3] 提取链接: {len(all_channels)} 条 | 开启深度识别与筛选...")
    
    best_sources = {}
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(check_channel, ch): ch for ch in all_channels}
        with tqdm(total=len(all_channels), desc="校验进度", bar_format='{l_bar}{bar:20}{r_bar}') as pbar:
            for f in as_completed(futures):
                res_ch, is_ok = f.result()
                if is_ok:
                    unique_key = (res_ch['epg_id'], res_ch['is_ultra'])
                    phys_score = res_ch['height'] * 1000000 + res_ch['bitrate']
                    if unique_key not in best_sources or phys_score > best_sources[unique_key]['phys_score']:
                        res_ch['phys_score'] = phys_score
                        best_sources[unique_key] = res_ch
                pbar.update(1)

    final_list = sorted(best_sources.values(), key=sort_key)

    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            logo_id = ch['epg_id'].replace('-', '')
            group = get_group(ch['name'])
            f.write(f'#EXTINF:-1 tvg-id="{ch["epg_id"]}" tvg-logo="https://live.fanmingming.com/tv/{logo_id}.png" group-title="{group}",{ch["name"]}\n{ch["url"]}\n')

    print(f"\n✅ 完成！最终入选 {len(final_list)} 个优质频道。")

if __name__ == "__main__":
    start_time = time.time()
    run()
    print(f"⏱️ 全程耗时: {int(time.time() - start_time)} 秒")