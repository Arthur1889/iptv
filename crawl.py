import os
import platform
import subprocess
import sys
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 0. 环境与依赖 =================
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

# ================= 1. 配置与工具 =================
CONFIG_FILE = "sources.json"

def load_sources():
    if not os.path.exists(CONFIG_FILE): return []
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f).get("urls", [])
    except: return []

SOURCE_URLS = load_sources()

GROUP_PRIORITY = {
    "央视频道": 1, "地方卫视": 2, "上海频道": 3, "港澳台": 4, 
    "电影/影院": 5, "体育/竞技": 6, "英文/国际": 7, "纪录/纪实": 8, "少儿/动画": 9 
}

# ================= 2. 核心功能函数 =================

def clean_channel_name(name, height=0, original_name=""):
    """
    清洗名称并根据物理参数或文字匹配进行打标
    """
    # 1. 物理识别（最高优先级）
    is_4k = height >= 2160
    is_8k = height >= 4320

    # 2. 文字匹配（回退机制：如果物理探测失败，检查原始名称）
    if height == 0:
        if re.search(r'8K', original_name, re.I): is_8k = True
        elif re.search(r'4K', original_name, re.I): is_4k = True

    # 3. 基础清洗
    clean_n = re.sub(r'(\[.*?\]|【.*?】|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|频道|画质)', '', name, flags=re.I)
    clean_n = clean_n.replace("CCTV", "CCTV-").replace("CCTV--", "CCTV-")
    clean_n = clean_n.strip().upper()
    
    # 4. 重新打标
    if is_8k: clean_n = f"{clean_n}-8K"
    elif is_4k: clean_n = f"{clean_n}-4K"
    
    return clean_n

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
    """
    探测逻辑：即使物理探测失败(h=0)，只要链接能通，也保留以便后续文字匹配
    """
    try:
        h, br = deep_analyze_stream(ch['url'])
        # 只要能获取到基本信息（即使高度为0，但ffprobe没报错，或者链接本身有效）
        # 这里为了严谨，如果 h=0，我们尝试用 requests 验证一下链接存活
        if h >= 360:
            ch['height'], ch['bitrate'] = h, br
            ch['name'] = clean_channel_name(ch['name'], height=h, original_name=ch['origin_name'])
            return ch, True
        else:
            # 回退：物理探测失败，但如果名字里带 4K/8K，我们放宽要求，通过 HTTP 状态码验证
            res = requests.head(ch['url'], timeout=3, headers={"User-Agent": ENV["ua"]})
            if res.status_code == 200:
                ch['height'], ch['bitrate'] = 0, 0
                ch['name'] = clean_channel_name(ch['name'], height=0, original_name=ch['origin_name'])
                # 只有文字匹配中含有 4K/8K 的才在 h=0 时保留
                if "-4K" in ch['name'] or "-8K" in ch['name']:
                    return ch, True
    except: pass
    return ch, False

def sort_key(ch):
    group_p = GROUP_PRIORITY.get(get_group(ch['name']), 99)
    name = ch['name']
    cctv_match = re.search(r'CCTV-(\d+)', name)
    cctv_num = int(cctv_match.group(1)) if cctv_match else 999
    score = ch.get('height', 0) * 10000000 + ch.get('bitrate', 0)
    return (group_p, cctv_num, name, -score)

def get_group(name):
    n = name.upper()
    if "CCTV" in n: return "央视频道"
    if "卫视" in n: return "地方卫视"
    if any(s in n for s in ["上海", "东方", "五星体育", "新闻综合", "纪实人文"]): return "上海频道"
    k_map = {"港澳台": ["翡翠", "TVB", "凤凰", "明珠"], "电影/影院": ["电影", "影院"], "体育/竞技": ["体育", "竞技"]}
    for group, keys in k_map.items():
        if any(k in n for k in keys): return group
    return "综合/其他"

# ================= 3. 主程序逻辑 =================

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
                    # 额外存储一个原始名称用于回退匹配
                    all_channels.append({"name": name, "origin_name": name, "url": link})
                    seen_urls.add(link)
        except: continue

    print(f"🚀 [2/3] 提取链接: {len(all_channels)} 条 | 开始物理探测 + 文字回退校验...")
    
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
                    
                    # 保留原则：4K/8K 全留
                    if "-4K" in res_ch['name'] or "-8K" in res_ch['name']:
                        unique_key = f"{res_ch['name']}_{res_ch['url']}"
                    else:
                        unique_key = res_ch['name']

                    if unique_key not in best_channels:
                        best_channels[unique_key] = res_ch
                    else:
                        new_score = res_ch['height'] * 1000 + res_ch['bitrate']
                        old_score = best_channels[unique_key]['height'] * 1000 + best_channels[unique_key]['bitrate']
                        if new_score > old_score:
                            best_channels[unique_key] = res_ch
                pbar.update(1)

    final_list = sorted(best_channels.values(), key=sort_key)
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            logo_id = ch['name'].replace('-4K','').replace('-8K','').replace('-', '')
            group = get_group(ch['name'])
            f.write(f'#EXTINF:-1 tvg-id="{ch["name"]}" tvg-logo="https://live.fanmingming.com/tv/{logo_id}.png" group-title="{group}",{ch["name"]}\n{ch["url"]}\n')

    print(f"\n✅ 完成！最终精选频道 {len(final_list)} 个")

if __name__ == "__main__":
    start_time = time.time()
    run()
    print(f"⏱️ 全程耗时: {int(time.time() - start_time)} 秒")