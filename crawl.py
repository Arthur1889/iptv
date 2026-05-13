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

# ================= 1. 环境与依赖 =================
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
        "timeout_pre": 2, # 预检超时：2秒
        "timeout_full": 5, # 深度探测超时：5秒
        "workers": 80     # 预检可以开更多并发
    }
    if sys_type == "Windows" and os.path.exists(r"C:\ffmpeg\bin\ffprobe.exe"):
        config["ffprobe"] = r"C:\ffmpeg\bin\ffprobe.exe"
    return config

ENV = get_env_config()

# ================= 2. 核心配置 =================
CONFIG_FILE = "sources.json"
SOURCE_URLS = []
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        SOURCE_URLS = json.load(f).get("urls", [])

GROUP_PRIORITY = {
    "央视频道": 1, "地方卫视": 2, "上海频道": 3, "港澳台": 4, 
    "电影/影院": 5, "体育/竞技": 6, "英文/国际": 7, "纪录/纪实": 8, "少儿/动画": 9 
}

# ================= 3. 功能函数 =================

def clean_channel_name(name, height=0):
    name = re.sub(r'(\[.*?\]|【.*?】|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|频道|画质)', '', name, flags=re.I)
    name = name.replace("CCTV", "CCTV-").replace("CCTV--", "CCTV-")
    name = name.strip().upper()
    if height >= 2160:
        if "4K" not in name: name = f"{name}-4K"
    return name

def get_group(name):
    n = name.upper()
    if "CCTV" in n: return "央视频道"
    if "卫视" in n: return "地方卫视"
    if any(s in n for s in ["上海", "东方", "五星体育", "新闻综合", "纪实人文"]): return "上海频道"
    k_map = {
        "港澳台": ["翡翠", "TVB", "凤凰", "明珠", "J2", "HK", "澳门", "台湾", "年代", "中天", "纬来", "东森"],
        "电影/影院": ["电影", "影院", "剧场", "影视", "CHC", "动作", "喜剧", "经典"],
        "体育/竞技": ["体育", "竞技", "足球", "篮球", "高尔夫", "网球", "赛事"]
    }
    for group, keys in k_map.items():
        if any(k in n for k in keys): return group
    return "综合/其他"

def deep_analyze_stream(url):
    """深度探测：仅对存活链接运行"""
    cmd = [
        ENV["ffprobe"], '-v', 'error', '-probesize', '128000', 
        '-analyzeduration', '300000', '-user_agent', ENV["ua"], 
        '-show_entries', 'stream=width,height,bit_rate', '-of', 'json', 
        '-select_streams', 'v:0', '-timeout', '3000000', url 
    ]
    try:
        cf = subprocess.CREATE_NO_WINDOW if ENV["os"] == "Windows" else 0
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=ENV["timeout_full"], creationflags=cf)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if 'streams' in data and data['streams']:
                s = data['streams'][0]
                return int(s.get('height', 0)), int(s.get('bit_rate', 0)) or 0
    except: pass
    return 0, 0

def pre_check_url(ch, session):
    """第一阶段：快速网络预检"""
    try:
        res = session.head(ch['url'], timeout=ENV["timeout_pre"], allow_redirects=True)
        if res.status_code == 200:
            return ch, True
    except: pass
    return ch, False

def sort_key(ch):
    group_p = GROUP_PRIORITY.get(ch['group'], 99)
    name = ch['name']
    cctv_match = re.search(r'CCTV-(\d+)', name)
    cctv_num = int(cctv_match.group(1)) if cctv_match else 999
    quality_score = ch.get('height', 0) * 10000000 + ch.get('bitrate', 0)
    return (group_p, cctv_num, name, -quality_score)

# ================= 4. 主流程 =================
def run():
    if not SOURCE_URLS: return

    all_channels = []
    seen_urls = set()
    session = requests.Session()
    session.headers.update({"User-Agent": ENV["ua"]})

    # 步骤 1：提取
    print(f"\n📡 [1/4] 正在从 {len(SOURCE_URLS)} 个列表提取链接...")
    for url in SOURCE_URLS:
        try:
            r = session.get(url, timeout=10, verify=False)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)(?:\n|$)', r.text)
            for name, link in matches:
                link = link.strip()
                if link not in seen_urls:
                    clean_n = clean_channel_name(name)
                    all_channels.append({"name": clean_n, "url": link})
                    seen_urls.add(link)
        except: pass

    # 步骤 2：快速预检
    print(f"⚡ [2/4] 第一阶段：正在快速筛选存活链接 (总数: {len(all_channels)})...")
    alive_channels = []
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(pre_check_url, ch, session): ch for ch in all_channels}
        for f in tqdm(as_completed(futures), total=len(all_channels), desc="预检进度"):
            res_ch, is_alive = f.result()
            if is_alive: alive_channels.append(res_ch)

    # 步骤 3：深度探测
    print(f"🔍 [3/4] 第二阶段：正在深度探测有效源质量 (存活数: {len(alive_channels)})...")
    best_channels = {}
    with ThreadPoolExecutor(max_workers=30) as executor: # 深度探测降低并发防卡死
        futures = {executor.submit(deep_analyze_stream, ch['url']): ch for ch in alive_channels}
        for f in tqdm(as_completed(futures), total=len(alive_channels), desc="深度探测"):
            h, br = f.result()
            if h >= 360:
                ch = futures[f]
                ch['height'], ch['bitrate'] = h, br
                ch['name'] = clean_channel_name(ch['name'], height=h)
                ch['group'] = get_group(ch['name'])
                # 择优去重逻辑
                u_key = f"{ch['name']}_{ch['height']}"
                if u_key not in best_channels or br > best_channels[u_key]['bitrate']:
                    best_channels[u_key] = ch

    # 步骤 4：保存
    final_list = sorted(best_channels.values(), key=sort_key)
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            logo_id = ch['name'].replace('-4K', '').replace('-', '')
            f.write(f'#EXTINF:-1 tvg-id="{ch["name"]}" tvg-logo="https://live.fanmingming.com/tv/{logo_id}.png" group-title="{ch["group"]}",{ch["name"]}\n{ch["url"]}\n')

    print(f"\n✅ 完成！最终入选频道: {len(final_list)}")

if __name__ == "__main__":
    start_time = time.time()
    run()
    print(f"⏱️ 总耗时: {int(time.time() - start_time)} 秒")