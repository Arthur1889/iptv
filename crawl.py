import requests
import re
import subprocess
import json
import time
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1. 抓取源
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
    """基础清洗：剔除原始源里的杂质标签"""
    clean = re.sub(r'(\[.*?\]|【.*?】|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|B8|C7|A\d+|NOT 24/7)', '', name, flags=re.I)
    return clean.strip().rstrip('-').strip()

def get_group(name):
    name_up = name.upper()
    if "CCTV" in name_up or "央视" in name: return "央视频道"
    if "卫视" in name: return "地方卫视"
    if any(s in name for s in ["上海", "东方", "五星体育", "新闻综合"]): return "上海频道"
    return "其他频道"

def deep_analyze_stream(url):
    """深度探测分辨率和码率，用于择优"""
    win_path = r"C:\ffmpeg\bin\ffprobe.exe"
    ffprobe_path = win_path if os.path.exists(win_path) else "ffprobe"
    
    cmd = [
        ffprobe_path, '-v', 'error', 
        '-show_entries', 'stream=width,height,bit_rate', 
        '-of', 'json', '-select_streams', 'v:0',
        '-analyzeduration', '5000000', '-probesize', '5000000',
        '-timeout', '5000000', url
    ]
    try:
        extra_args = {}
        if os.name == 'nt' and os.path.exists(win_path):
            extra_args['creationflags'] = subprocess.CREATE_NO_WINDOW
            
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, **extra_args)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if 'streams' in data and len(data['streams']) > 0:
                s = data['streams'][0]
                h = s.get('height', 0)
                br = int(s.get('bit_rate', 0)) if s.get('bit_rate') else 0
                return h, br
    except:
        pass
    return 0, 0

def check_channel(ch):
    """检测并记录质量数据"""
    try:
        res = requests.get(ch['url'], timeout=3, stream=True)
        if res.status_code == 200:
            h, br = deep_analyze_stream(ch['url'])
            if h > 0:
                ch['height'] = h
                ch['bitrate'] = br
                return ch, True
    except:
        pass
    return ch, False

def fetch_and_process():
    all_channels = []
    print(">>> 正在抓取源数据...")
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, timeout=10)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)(?:\n|$)', r.text)
            for name, link in matches:
                all_channels.append({
                    "name": clean_channel_name(name), 
                    "url": link.strip(), 
                    "group": get_group(name)
                })
        except: continue

    total = len(all_channels)
    print(f">>> 正在进行质量对比择优 (总数: {total})...")
    
    best_channels = {} # 用于存储每个频道最强的那一个源

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(check_channel, ch) for ch in all_channels]
        for f in as_completed(futures):
            res_ch, is_ok = f.result()
            if is_ok:
                name = res_ch['name']
                # 择优逻辑：如果该频道已存在，比较 分辨率 -> 码率
                if name not in best_channels:
                    best_channels[name] = res_ch
                else:
                    current_best = best_channels[name]
                    # 如果新发现的源分辨率更高，或者分辨率一样但码率更高，则替换
                    if (res_ch['height'] > current_best['height']) or \
                       (res_ch['height'] == current_best['height'] and res_ch['bitrate'] > current_best['bitrate']):
                        best_channels[name] = res_ch
            
            sys.stdout.write(f"\r[已探测: {len(best_channels)} 个唯一频道] | 总进度: {int((list(futures).index(f)+1)/total*100)}%")
            sys.stdout.flush()

    results = list(best_channels.values())
    # 最终排序：组优先级 -> 分辨率从高到低
    results.sort(key=lambda x: (GROUP_PRIORITY.get(x['group'], 99), -x.get('height', 0)))
    return results

def save_m3u(channels):
    """保存为干净的列表"""
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in channels:
            # 这里不再写后缀，只保留纯净名称
            f.write(f'#EXTINF:-1 group-title="{ch["group"]}",{ch["name"]}\n{ch["url"]}\n')

if __name__ == "__main__":
    start_time = time.time()
    final_data = fetch_and_process()
    save_m3u(final_data)
    print(f"\n🎉 择优去重完成！共保留 {len(final_data)} 个最清晰的唯一频道。耗时: {int(time.time() - start_time)}s")
