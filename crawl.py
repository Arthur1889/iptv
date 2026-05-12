import requests
import re
import subprocess
import json
import time
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm  # 导入进度条库

# 1. 配置
EPG_URL = "https://live.fanmingming.com/e.xml"
SOURCE_URLS = [
    "https://live.fanmingming.com/tv/m3u/ipv6.m3u",
    "https://iptv-org.github.io/iptv/countries/cn.m3u",
    "https://raw.githubusercontent.com/frankwuzp/iptv-cn/master/tv-ipv4-cn.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u",
    "https://raw.githubusercontent.com/plsy1/iptv/main/multicast/multicast-qingdao.m3u",
    "https://raw.githubusercontent.com/xcc360/SHCU-TV/refs/heads/main/IPTV.m3u",
    "https://raw.githubusercontent.com/babylife/China-ShangHai-IPTV-list/master/IPTV_Enhanced_change.m3u"
]

GROUP_PRIORITY = {"央视频道": 1, "地方卫视": 2, "上海频道": 3, "其他频道": 4}

def clean_channel_name(name):
    name = re.sub(r'(\[.*?\]|【.*?】|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|B8|C7|A\d+|NOT 24/7|频道)', '', name, flags=re.I)
    name = name.replace("CCTV", "CCTV-")
    name = re.sub(r'-+', '-', name)
    return name.strip().rstrip('-').strip()

def get_group(name):
    if "CCTV" in name.upper() or "央视" in name: return "央视频道"
    if "卫视" in name: return "地方卫视"
    if any(s in name for s in ["上海", "东方", "五星体育", "新闻综合"]): return "上海频道"
    return "其他频道"

def sort_key(ch):
    group_p = GROUP_PRIORITY.get(ch['group'], 99)
    name = ch['name'].upper()
    cctv_num = 999
    if "CCTV-" in name:
        match = re.search(r'CCTV-(\d+)', name)
        if match: cctv_num = int(match.group(1))
        elif "5+" in name: cctv_num = 5.5
        elif "奥林匹克" in name: cctv_num = 16.5
    return (group_p, cctv_num, -ch.get('height', 0))

def deep_analyze_stream(url):
    win_path = r"C:\ffmpeg\bin\ffprobe.exe"
    ffprobe_path = win_path if os.path.exists(win_path) else "ffprobe"
    cmd = [
        ffprobe_path, '-v', 'error', '-show_entries', 'stream=width,height,bit_rate', 
        '-of', 'json', '-select_streams', 'v:0', '-timeout', '5000000', url
    ]
    try:
        args = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, **args)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if 'streams' in data:
                s = data['streams'][0]
                return s.get('height', 0), int(s.get('bit_rate', 0)) if s.get('bit_rate') else 0
    except: pass
    return 0, 0

def check_channel(ch):
    try:
        res = requests.get(ch['url'], timeout=3, stream=True)
        if res.status_code == 200:
            h, br = deep_analyze_stream(ch['url'])
            if h > 0:
                ch['height'], ch['bitrate'] = h, br
                return ch, True
    except: pass
    return ch, False

def fetch_and_process():
    all_channels = []
    print("\n[1/3] 🔍 正在从各数据源抓取频道列表...")
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, timeout=10)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)(?:\n|$)', r.text)
            for name, link in matches:
                clean_n = clean_channel_name(name)
                all_channels.append({"name": clean_n, "url": link.strip(), "group": get_group(clean_n)})
        except: continue

    total = len(all_channels)
    print(f"[2/3] 🚀 开始深度分析与去重 (共 {total} 个源)...")
    
    best_channels = {}
    success_count = 0
    
    # 使用 tqdm 创建可视化进度条
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(check_channel, ch): ch for ch in all_channels}
        
        # 这里的 pbar 就是你的可视化窗口
        with tqdm(total=total, desc="分析进度", unit="个", bar_format='{l_bar}{bar:40}{r_bar}') as pbar:
            for f in as_completed(futures):
                res_ch, is_ok = f.result()
                if is_ok:
                    name = res_ch['name']
                    if name not in best_channels or \
                       (res_ch['height'] > best_channels[name]['height']) or \
                       (res_ch['height'] == best_channels[name]['height'] and res_ch['bitrate'] > best_channels[name]['bitrate']):
                        best_channels[name] = res_ch
                        success_count = len(best_channels)
                
                # 更新进度条右侧的统计信息
                pbar.set_postfix({"有效频道": success_count})
                pbar.update(1)

    print(f"\n[3/3] 💾 正在排序并保存结果...")
    results = list(best_channels.values())
    results.sort(key=sort_key)
    return results

def save_m3u(channels):
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write(f'#EXTM3U x-tvg-url="{EPG_URL}"\n')
        for ch in channels:
            tvg_name = ch['name'].replace("-", "")
            logo = f"https://live.fanmingming.com/tv/{tvg_name}.png"
            f.write(f'#EXTINF:-1 tvg-id="{ch["name"]}" tvg-name="{ch["name"]}" tvg-logo="{logo}" group-title="{ch["group"]}",{ch["name"]}\n')
            f.write(f'{ch["url"]}\n')

if __name__ == "__main__":
    start_time = time.time()
    data = fetch_and_process()
    save_m3u(data)
    elapsed = time.time() - start_time
    print(f"\n✨ 任务完成！")
    print(f"统计：抓取 {len(data)} 个优质频道 | 总耗时 {int(elapsed)}s")
    print(f"结果已同步至: tv.m3u")