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
    config = {"os": sys_type, "ffprobe": "ffprobe", "ffmpeg": "ffmpeg", "timeout": 15, "workers": 8}
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

# ================= 2. 核心清洗与校验逻辑 =================

def get_standard_name(origin_name):
    name = re.sub(r'\.(cn|hk|tw|us|uk|org)$', '', origin_name.strip(), flags=re.I)
    for main_name, aliases in ALIAS_MAP.items():
        if any(a.strip().upper() in name.upper() for a in aliases): return main_name
    return name

def clean_channel_name(name, height=0, original_name=""):
    noise = (
        r'(HD|高清|超高清|蓝光|频道|\[.*?\]|\(.*?\)|\d+[PpIi]|'
        r'Geo-blocked|Not 24/7|HEVC|H\.264|H\.265|'
        r'\(备用\)|\d+fps|'
        r'[-_]\d+$)'
    )
    source_text = original_name if original_name else name
    cleaned = re.sub(noise, '', source_text, flags=re.I).strip()
    cleaned = cleaned.rstrip('- ').strip()
    
    base_name = get_standard_name(cleaned)
    base_name = re.sub(r'(-4K|-8K|4K|8K|超高清|HD|高清)$', '', base_name, flags=re.I).strip()
    
    is_ultra = height >= 2160 or re.search(r'4K|8K|2160p', source_text, re.I)
    return (f"{base_name}-4K" if is_ultra else base_name), base_name, is_ultra

def deep_analyze_stream(url):
    cmd = [ENV["ffprobe"], '-v', 'error', '-show_entries', 'format_tags=service_name:stream=width,height,bit_rate', '-of', 'json', '-select_streams', 'v:0', url]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=ENV["timeout"], errors='ignore')
        data = json.loads(res.stdout)
        s = data['streams'][0]
        return int(s.get('height', 0)), int(s.get('bit_rate', 0))
    except: return 0, 0

def check_channel(ch):
    h, br = deep_analyze_stream(ch['url'])
    if h >= 360:
        f_name, b_name, is_u = clean_channel_name(ch['name'], h, ch['origin_name'])
        ch.update({'height': h, 'bitrate': br, 'name': f_name, 'epg_id': b_name, 'is_ultra': is_u})
        return ch, True
    return ch, False

# ================= 3. 增强排序与过滤逻辑 =================

def get_group_name(n):
    n = n.upper()
    if "4K" in n or "8K" in n: return "4K频道"
    if "CCTV" in n: return "央视频道"
    if "卫视" in n: return "地方卫视"
    if any(k in n for k in ["HBO", "CNN", "NHK", "TVB", "翡翠", "凤凰"]): return "港澳台"
    return "综合频道"

def get_cctv_rank(name):
    """【优化 1】针对 CCTV 频道的精准数字排序"""
    if "CCTV" not in name.upper(): return 999
    num_match = re.search(r'CCTV[-]?(\d+)', name, re.I)
    if num_match:
        return int(num_match.group(1))
    # 4K/8K 频道排在数字台后面
    if "4K" in name.upper(): return 100
    if "8K" in name.upper(): return 101
    return 200

def run():
    if not os.path.exists(CONFIG_FILE): return
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        SOURCE_URLS = json.load(f).get("urls", [])

    all_channels = []
    seen_urls = set()
    
    print(f"\n📡 [1/3] 提取链接 (过滤直播室)...")
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, timeout=10, verify=False)
            lines = r.text.split('\n')
            for i in range(len(lines)):
                if lines[i].startswith('#EXTINF:'):
                    inf_line = lines[i]
                    link = lines[i+1].strip() if i+1 < len(lines) else ""
                    if not link.startswith('http') or link in seen_urls: continue
                    
                    # 基础信息解析
                    tid_m = re.search(r'tvg-id="(.*?)"', inf_line)
                    raw_name = inf_line.split(',')[-1].strip()
                    tid = tid_m.group(1) if tid_m else ""
                    if tid and re.match(r'\d{4}-\d{2}-\d{2}', tid): tid = ""
                    final_name = tid if tid else raw_name
                    
                    # 【优化 2】直播室逻辑：如果名称包含直播室/影院等关键词，直接跳过
                    if any(k in final_name for k in ["直播室", "影院", "轮播", "专题"]):
                        continue
                    
                    all_channels.append({"name": final_name, "origin_name": raw_name, "url": link})
                    seen_urls.add(link)
        except: continue

    print(f"🚀 [2/3] 校验与择优 (共 {len(all_channels)} 条)...")
    best_sources = {}
    valid_count = 0
    
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(check_channel, ch): ch for ch in all_channels}
        pbar_fmt = '{l_bar}{bar:20}{r_bar} {n_fmt}/{total_fmt} [{percentage:3.0f}%] 优质源:{postfix}'
        with tqdm(total=len(all_channels), desc="探测进度", bar_format=pbar_fmt) as pbar:
            for f in as_completed(futures):
                try:
                    res_ch, is_ok = f.result()
                    if is_ok:
                        valid_count += 1
                        pbar.set_postfix_str(str(valid_count))
                        ukey = res_ch['epg_id']
                        score = res_ch['height'] * 1000000 + res_ch['bitrate']
                        if ukey not in best_sources or score > best_sources[ukey]['phys_score']:
                            res_ch['phys_score'] = score
                            best_sources[ukey] = res_ch
                except: pass
                pbar.update(1)

    print(f"📦 [3/3] 生成有序列表与台标...")
    
    # 【排序逻辑整合】
    def final_sort_key(ch):
        g_name = get_group_name(ch['name'])
        group_rank = GROUP_PRIORITY.get(g_name, 99)
        cctv_rank = get_cctv_rank(ch['name'])
        # 排序规则：分组优先级 > 央视数字顺序 > 画质分数
        return (group_rank, cctv_rank, -ch['phys_score'])

    final_list = sorted(best_sources.values(), key=final_sort_key)

    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            g_name = get_group_name(ch['name'])
            logo_name = ch['epg_id'].replace('-', '')
            f.write(f'#EXTINF:-1 tvg-id="{ch["epg_id"]}" tvg-logo="https://live.fanmingming.com/tv/{logo_name}.png" group-title="{g_name}",{ch["name"]}\n{ch["url"]}\n')

    print(f"\n✅ 完成！最终入选 {len(best_sources)} 个频道。")

if __name__ == "__main__":
    start_time = time.time()
    run()
    duration = int(time.time() - start_time)
    print(f"⏱️ 总耗时: {duration // 60}分{duration % 60}秒")