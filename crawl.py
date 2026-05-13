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
        "timeout": 12, # 增加超时，给 8K 和长链接足够时间
        "workers": 50 
    }
    if sys_type == "Windows" and os.path.exists(r"C:\ffmpeg\bin\ffprobe.exe"):
        config["ffprobe"] = r"C:\ffmpeg\bin\ffprobe.exe"
    return config

ENV = get_env_config()

# ================= 2. 核心配置 =================
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

# ================= 3. 功能函数 =================

def clean_channel_name(name, height=0):
    """
    清洗名称并根据物理像素打标 (4K/8K)
    """
    # 基础清洗逻辑：去除名称中自带的画质标签，防止重复叠加
    name = re.sub(r'(\[.*?\]|【.*?】|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|频道|画质)', '', name, flags=re.I)
    name = name.replace("CCTV", "CCTV-").replace("CCTV--", "CCTV-")
    name = name.strip().upper()
    
    # 物理标注逻辑
    if height >= 4320:
        if "8K" not in name: name = f"{name}-8K"
    elif height >= 2160:
        if "4K" not in name: name = f"{name}-4K"
    return name

def get_group(name):
    n = name.upper()
    if "CCTV" in n: return "央视频道"
    if "卫视" in n: return "地方卫视"
    if any(s in n for s in ["上海", "东方", "五星体育", "新闻综合", "纪实人文"]): return "上海频道"
    
    k_map = {
        "港澳台": ["翡翠", "TVB", "凤凰", "明珠", "J2", "HK", "澳门", "台湾"],
        "电影/影院": ["电影", "影院", "剧场", "影视", "CHC", "动作"],
        "体育/竞技": ["体育", "竞技", "足球", "篮球", "赛事"],
        "纪录/纪实": ["纪录", "纪实", "探索", "人文", "地理", "世界"]
    }
    for group, keys in k_map.items():
        if any(k in n for k in keys): return group
    return "综合/其他"

def deep_analyze_stream(url):
    """
    针对 8K/4K 优化：加大探测包 size 并延长分析时间
    """
    cmd = [
        ENV["ffprobe"], '-v', 'error', 
        '-probesize', '2048000',      # 调大至 2MB，确保 8K 解析
        '-analyzeduration', '3000000', # 增加分析时长至 3 秒
        '-user_agent', ENV["ua"], 
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
    直接探测模式：跳过 HEAD 预检以兼容内网运营商源
    """
    try:
        h, br = deep_analyze_stream(ch['url'])
        if h >= 360:
            ch['height'], ch['bitrate'] = h, br
            # 根据物理分辨率重新修正名字 (识别 8K/4K)
            ch['name'] = clean_channel_name(ch['name'], height=h)
            ch['group'] = get_group(ch['name'])
            return ch, True
    except: pass
    return ch, False

def sort_key(ch):
    group_p = GROUP_PRIORITY.get(ch['group'], 99)
    name = ch['name']
    cctv_match = re.search(r'CCTV-(\d+)', name)
    cctv_num = int(cctv_match.group(1)) if cctv_match else 999
    # 分辨率权重最高，确保 8K/4K 在同一频道中排在最前
    score = ch.get('height', 0) * 10000000 + ch.get('bitrate', 0)
    return (group_p, cctv_num, name, -score)

# ================= 4. 主流程 =================
def run():
    if not SOURCE_URLS:
        print("⚠️ 没有可用的源链接，请检查 sources.json！")
        return

    all_channels = []
    seen_urls = set()
    session = requests.Session()
    session.headers.update({"User-Agent": ENV["ua"]})

    # [1/3] 提取链接
    print(f"\n📡 [1/3] 正在从 {len(SOURCE_URLS)} 个源提取链接...")
    for url in SOURCE_URLS:
        try:
            r = session.get(url, timeout=12, verify=False)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)(?:\n|$)', r.text)
            for name, link in matches:
                link = link.strip()
                if link not in seen_urls:
                    all_channels.append({"name": name, "url": link})
                    seen_urls.add(link)
        except: continue

    # [2/3] 探测画质
    print(f"🚀 [2/3] 提取链接: {len(all_channels)} 条 | 开始物理探测...")
    
    best_channels = {}
    valid_count = 0
    
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(check_channel, ch): ch for ch in all_channels}
        
        # UI 优化：显示百分比、当前进度/总数、实时优质源计数
        with tqdm(total=len(all_channels), desc="探测进度", bar_format='{l_bar}{bar:20}{r_bar} {n_fmt}/{total_fmt} [{percentage:3.0f}%] 优质源:{postfix}') as pbar:
            for f in as_completed(futures):
                res_ch, is_ok = f.result()
                if is_ok:
                    valid_count += 1
                    pbar.set_postfix_str(str(valid_count))
                    
                    # --- 核心保留原则 ---
                    # 4K (2160P) 及 8K (4320P) 采取“全量保留”：unique_key 包含 URL
                    if res_ch['height'] >= 2160:
                        unique_key = f"{res_ch['name']}_{res_ch['url']}"
                    else:
                        # 普通频道 (1080P及以下) 采取“择优留一”：unique_key 仅频道名
                        unique_key = res_ch['name']

                    if unique_key not in best_channels:
                        best_channels[unique_key] = res_ch
                    else:
                        # 如果是同名普通频道，保留质量更好的 (分辨率 * 1000 + 码率)
                        curr_score = res_ch['height'] * 1000 + res_ch['bitrate']
                        best_score = best_channels[unique_key]['height'] * 1000 + best_channels[unique_key]['bitrate']
                        if curr_score > best_score:
                            best_channels[unique_key] = res_ch
                pbar.update(1)

    # [3/3] 排序并保存
    final_list = sorted(best_channels.values(), key=sort_key)
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            # 台标匹配逻辑：剔除画质后缀以保证 logo 正常显示
            logo_id = ch['name'].replace('-4K','').replace('-8K','').replace('-', '')
            f.write(f'#EXTINF:-1 tvg-id="{ch["name"]}" tvg-logo="https://live.fanmingming.com/tv/{logo_id}.png" group-title="{ch["group"]}",{ch["name"]}\n{ch["url"]}\n')

    print("\n" + "="*50)
    print(f"✅ 探测完成！存活优质源: {valid_count} | 最终精选频道: {len(final_list)}")
    print(f"📦 结果已存入: tv.m3u")
    print("="*50 + "\n")

if __name__ == "__main__":
    start_time = time.time()
    run()
    print(f"⏱️ 总耗时: {int(time.time() - start_time)} 秒")