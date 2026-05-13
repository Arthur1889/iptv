import os
import platform
import subprocess
import sys
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 0. 禁用 SSL 警告 =================
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
        "timeout": 12, 
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
    """
    加载 name.json 并解析为别名映射字典
    支持正则匹配
    """
    alias_dict = {}
    if not os.path.exists(NAME_JSON):
        return alias_dict
    try:
        with open(NAME_JSON, 'r', encoding='utf-8') as f:
            # 过滤掉注释行
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

GROUP_PRIORITY = {
    "央视频道": 1, "地方卫视": 2, "上海频道": 3, "港澳台": 4, 
    "电影/影院": 5, "体育/竞技": 6, "英文/国际": 7, "纪录/纪实": 8, "少儿/动画": 9 
}

# ================= 2. 核心逻辑函数 =================

def get_standard_name(origin_name):
    """
    根据别名表将原始名称标准化
    """
    name_upper = origin_name.strip().upper()
    for main_name, aliases in ALIAS_MAP.items():
        for alias in aliases:
            alias = alias.strip()
            # 处理正则表达式
            if alias.startswith("re:"):
                pattern = alias[3:]
                try:
                    if re.search(pattern, origin_name):
                        return main_name
                except: continue
            # 处理普通字符串匹配
            elif alias.upper() in name_upper or name_upper in alias.upper():
                return main_name
    return origin_name

def clean_channel_name(name, height=0, original_name=""):
    """
    综合别名映射与物理像素打标
    """
    # 1. 首先通过别名映射表获取标准名
    standard_name = get_standard_name(original_name if original_name else name)
    
    # 2. 物理与文字识别 (4K/8K)
    is_4k = height >= 2160 or re.search(r'4K', original_name, re.I)
    is_8k = height >= 4320 or re.search(r'8K', original_name, re.I)

    # 3. 基础清洗（针对未命中别名表的频道）
    if standard_name == name:
        standard_name = re.sub(r'(\[.*?\]|【.*?】|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|频道|画质)', '', standard_name, flags=re.I)
        standard_name = standard_name.replace("CCTV", "CCTV-").replace("CCTV--", "CCTV-")
    
    standard_name = standard_name.strip().upper()
    
    # 4. 重新打标
    if is_8k: standard_name = f"{standard_name}-8K"
    elif is_4k: standard_name = f"{standard_name}-4K"
    
    return standard_name

def deep_analyze_stream(url):
    cmd = [
        ENV["ffprobe"], '-v', 'error', '-probesize', '2048000', 
        '-analyzeduration', '3000000', '-user_agent', ENV["ua"], 
        '-show_entries', 'stream=width,height,bit_rate', '-of', 'json', 
        '-select_streams', 'v:0', '-timeout', '10000000', url 
    ]
    try:
        cf = subprocess.CREATE_NO_WINDOW if ENV["os"] == "Windows" else 0
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=ENV["timeout"], creationflags=cf)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if 'streams' in data and data['streams']:
                s = data['streams'][0]
                return int(s.get('height', 0)), int(s.get('bit_rate', 0)) or 0
    except: pass
    return 0, 0

def check_channel(ch):
    try:
        h, br = deep_analyze_stream(ch['url'])
        # 存活判定：有物理分辨率 或 虽物理探测失败但名字带4K/8K且链接通畅
        if h >= 360:
            ch['height'], ch['bitrate'] = h, br
            ch['name'] = clean_channel_name(ch['name'], height=h, original_name=ch['origin_name'])
            return ch, True
        else:
            res = requests.head(ch['url'], timeout=3, headers={"User-Agent": ENV["ua"]}, verify=False)
            if res.status_code == 200:
                ch['height'], ch['bitrate'] = 0, 0
                ch['name'] = clean_channel_name(ch['name'], height=0, original_name=ch['origin_name'])
                if "-4K" in ch['name'] or "-8K" in ch['name']:
                    return ch, True
    except: pass
    return ch, False

def get_group(name):
    n = name.upper()
    if "CCTV" in n: return "央视频道"
    if "卫视" in n: return "地方卫视"
    if any(s in n for s in ["上海", "东方", "新闻综合", "纪实人文"]): return "上海频道"
    k_map = {"港澳台": ["翡翠", "TVB", "凤凰", "明珠", "J2", "HK", "澳门", "台湾"], "电影/影院": ["电影", "影院", "CHC"]}
    for group, keys in k_map.items():
        if any(k in n for k in keys): return group
    return "综合/其他"

def sort_key(ch):
    group_p = GROUP_PRIORITY.get(get_group(ch['name']), 99)
    name = ch['name']
    cctv_match = re.search(r'CCTV-(\d+)', name)
    cctv_num = int(cctv_match.group(1)) if cctv_match else 999
    score = ch.get('height', 0) * 10000000 + ch.get('bitrate', 0)
    return (group_p, cctv_num, name, -score)

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
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)(?:\n|$)', r.text)
            for name, link in matches:
                link = link.strip()
                if link not in seen_urls:
                    all_channels.append({"name": name, "origin_name": name, "url": link})
                    seen_urls.add(link)
        except: continue

    print(f"🚀 [2/3] 提取链接: {len(all_channels)} 条 | 基于别名表与物理探测校验...")
    
    best_channels = {}
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
                    
                    # 4K/8K 频道以 URL 为 Key 全量保留；普通频道以标准名为 Key 择优留一
                    if "-4K" in res_ch['name'] or "-8K" in res_ch['name']:
                        u_key = f"{res_ch['name']}_{res_ch['url']}"
                    else:
                        u_key = res_ch['name']

                    if u_key not in best_channels or (res_ch['height'] * 1000 + res_ch['bitrate'] > 
                                                     best_channels[u_key]['height'] * 1000 + best_channels[u_key]['bitrate']):
                        best_channels[u_key] = res_ch
                pbar.update(1)

    final_list = sorted(best_channels.values(), key=sort_key)
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            # Logo ID 逻辑：使用映射后的主名，并剔除画质后缀
            clean_id = ch['name'].split('-4K')[0].split('-8K')[0].replace('-', '')
            group = get_group(ch['name'])
            f.write(f'#EXTINF:-1 tvg-id="{ch["name"]}" tvg-logo="https://live.fanmingming.com/tv/{clean_id}.png" group-title="{group}",{ch["name"]}\n{ch["url"]}\n')

    print(f"\n✅ 完成！最终入选 {len(final_list)} 个频道。")

if __name__ == "__main__":
    start_time = time.time()
    run()
    print(f"⏱️ 全程耗时: {int(time.time() - start_time)} 秒")