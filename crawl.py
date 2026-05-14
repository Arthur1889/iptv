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
# 屏蔽 Torch 警告和 SSL 报错
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

# 分组优先级设定 (越小越靠前)
GROUP_PRIORITY = {
    "4K频道": 1,
    "央视频道": 2,
    "地方卫视": 3,
    "山东频道": 4,
    "港澳台": 5,
    "数字频道": 6,
    "综合频道": 10
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

# ================= 2. 核心校验逻辑 =================

def get_standard_name(origin_name):
    name = re.sub(r'\.(cn|hk|tw|us|uk|org)$', '', origin_name.strip(), flags=re.I)
    for main_name, aliases in ALIAS_MAP.items():
        if any(a.strip().upper() in name.upper() for a in aliases): return main_name
    return name

def clean_channel_name(name, height=0, original_name=""):
    noise = r'(HD|高清|超高清|蓝光|频道|\(备用\)|\(\d+[Pp]\)|\[\d+[Pp]\]|-\d+[Pp]|\d+[Pp])'
    cleaned = re.sub(noise, '', original_name if original_name else name, flags=re.I).strip()
    base_name = get_standard_name(cleaned)
    base_name = re.sub(r'(-4K|-8K|4K|8K|超高清|HD|高清)$', '', base_name, flags=re.I).strip()
    is_ultra = height >= 2160 or re.search(r'4K|8K', original_name, re.I)
    return (f"{base_name}-4K" if is_ultra else base_name), base_name, is_ultra

def visual_verify(url, target_name):
    tmp_img = f"shot_{int(time.time()*1000)}.jpg"
    cmd = [ENV["ffmpeg"], "-y", "-t", "3", "-i", url, "-vf", "crop=350:180:0:0", "-frames:v", "1", tmp_img]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, errors='ignore')
        if os.path.exists(tmp_img):
            content = "".join(READER.readtext(tmp_img, detail=0)).upper()
            os.remove(tmp_img)
            core = re.sub(r'[-频道]', '', target_name).upper()
            if any(k in content for k in ["CCTV", "卫视", core, "TV"]): return True, 1.3
            return False, 0.4
    except:
        if os.path.exists(tmp_img): os.remove(tmp_img)
    return False, 1.0

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
        v_weight = 1.0
        if any(k in b_name for k in ["CCTV", "卫视"]): _, v_weight = visual_verify(ch['url'], b_name)
        ch.update({'height': h, 'bitrate': int(br * v_weight), 'name': f_name, 'epg_id': b_name, 'is_ultra': is_u})
        return ch, v_weight >= 0.4
    return ch, False

# ================= 3. 增强分组与主程序 =================

def get_group_name(n):
    n = n.upper()
    if "4K" in n or "8K" in n: return "4K频道"
    if "CCTV" in n: return "央视频道"
    if "卫视" in n: return "地方卫视"
    if "山东" in n: return "山东频道"
    if any(k in n for k in ["凤凰", "翡翠", "TVB", "HBO", "CNN", "NHK", "翡翠"]): return "港澳台"
    if any(k in n for k in ["电影", "剧场", "世界", "纪录", "风云", "兵团"]): return "数字频道"
    return "综合频道"

def run():
    if not os.path.exists(CONFIG_FILE): return
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        SOURCE_URLS = json.load(f).get("urls", [])

    all_channels = []
    seen_urls = set()
    print(f"\n📡 [1/3] 正在提取链接...")
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, timeout=10, verify=False)
            matches = re.findall(r'#EXTINF:.*?(?:tvg-id="(.*?)")?.*?,(.*?)\n(http.*?)(?:\n|$)', r.text)
            for tid, name, link in matches:
                link_clean = link.strip()
                if link_clean not in seen_urls:
                    all_channels.append({"name": tid if tid else name, "origin_name": name, "url": link_clean})
                    seen_urls.add(link_clean)
        except: continue

    print(f"🚀 [2/3] 校验开始 (共 {len(all_channels)} 条)...")
    best_sources = {}
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(check_channel, ch): ch for ch in all_channels}
        with tqdm(total=len(all_channels), desc="探测进度", bar_format='{l_bar}{bar:20}{r_bar} {postfix}') as pbar:
            for f in as_completed(futures):
                try:
                    res_ch, is_ok = f.result()
                    if is_ok:
                        ukey = res_ch['epg_id']
                        score = res_ch['height'] * 1000000 + res_ch['bitrate']
                        if ukey not in best_sources or score > best_sources[ukey]['phys_score']:
                            res_ch['phys_score'] = score
                            best_sources[ukey] = res_ch
                except: pass
                pbar.update(1)

    print(f"📦 [3/3] 生成分组列表与图标...")
    # 排序逻辑：按 GROUP_PRIORITY 排序，组内按分数降序
    final_list = sorted(best_sources.values(), key=lambda x: (
        GROUP_PRIORITY.get(get_group_name(x['name']), 99),
        -x['phys_score']
    ))

    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            g_name = get_group_name(ch['name'])
            # 转换 ID 为无横杠格式以匹配台标库
            logo_name = ch['epg_id'].replace('-', '')
            f.write(f'#EXTINF:-1 tvg-id="{ch["epg_id"]}" tvg-logo="https://live.fanmingming.com/tv/{logo_name}.png" group-title="{g_name}",{ch["name"]}\n{ch["url"]}\n')

    print(f"\n✅ 完成！最终入选 {len(best_sources)} 个频道。")

if __name__ == "__main__":
    start_time = time.time()
    run()
    print(f"⏱️ 全程耗时: {int(time.time() - start_time)} 秒")