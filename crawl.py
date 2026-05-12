import requests
import re
import subprocess
import json
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1. 确认后的 7 个高权重源
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
    """剔除杂质标签"""
    clean = re.sub(r'(\[.*?\]|【.*?】|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|B8|C7|A\d+|NOT 24/7)', '', name, flags=re.I)
    return clean.strip().rstrip('-').strip()

def get_group(name):
    """频道分组逻辑"""
    name_up = name.upper()
    if "CCTV" in name_up or "央视" in name: return "央视频道"
    if "卫视" in name: return "地方卫视"
    if any(s in name for s in ["上海", "东方", "五星体育", "新闻综合"]): return "上海频道"
    return "其他频道"

def deep_analyze_stream(url):
    """使用 ffprobe 探测流信息 (Windows 路径已适配)"""
    ffprobe_path = r"C:\ffmpeg\bin\ffprobe.exe" 
    cmd = [
        ffprobe_path, '-v', 'error', '-show_entries', 'stream=width,height', 
        '-of', 'json', '-select_streams', 'v:0',
        '-analyzeduration', '5000000', '-probesize', '5000000',
        '-timeout', '5000000', url
    ]
    try:
        # Windows creationflags 防止黑窗口闪烁
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if 'streams' in data and len(data['streams']) > 0:
                s = data['streams'][0]
                return s.get('width', 0), s.get('height', 0)
    except:
        pass
    return 0, 0

def check_channel(ch):
    """综合验证逻辑"""
    try:
        # 第一步：快速 HTTP 请求
        res = requests.get(ch['url'], timeout=3, stream=True)
        if res.status_code == 200:
            # 第二步：ffprobe 深度检测
            w, h = deep_analyze_stream(ch['url'])
            if h > 0:
                if h >= 2160: label = "4K"
                elif h >= 1080: label = "1080P"
                elif h >= 720: label = "720P"
                else: label = "SD"
                ch['name'] = f"{ch['name']} [{label}]"
                ch['height'] = h
                return ch, True
    except:
        pass
    return ch, False

def fetch_and_process():
    all_channels = []
    print(">>> 正在从 7 个源抓取数据...")
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, timeout=10)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)(?:\n|$)', r.text)
            for name, link in matches:
                name, link = name.strip(), link.strip()
                if "127.0.0.1" in link: continue
                all_channels.append({"name": clean_channel_name(name), "url": link, "group": get_group(name)})
        except: continue

    total = len(all_channels)
    print(f">>> 开始深度分析 (总数: {total})...")
    valid_channels = []
    processed_count = 0
    success_count = 0

    # 限制并发为 8，确保 Windows 本地带宽不被撑爆
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(check_channel, ch) for ch in all_channels]
        for f in as_completed(futures):
            processed_count += 1
            res_ch, is_success = f.result()
            if is_success:
                success_count += 1
                valid_channels.append(res_ch)
            # 动态进度条
            sys.stdout.write(f"\r[进度: {processed_count}/{total}] | 成功: {success_count} | 正在测: {res_ch['name'][:15]}")
            sys.stdout.flush()

    print(f"\n\n>>> 分析结束！通过检测的频道: {len(valid_channels)}")

    # 去重
    unique_list = {}
    for ch in valid_channels:
        domain = ch['url'].split('/')[2] if '://' in ch['url'] else 'unknown'
        key = f"{ch['name']}_{domain}"
        if key not in unique_list: unique_list[key] = ch
    
    results = list(unique_list.values())
    # 排序：组优先级 -> 分辨率从高到低
    results.sort(key=lambda x: (GROUP_PRIORITY.get(x['group'], 99), -x.get('height', 0)))
    return results

def save_m3u(channels):
    """保存结果"""
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in channels:
            f.write(f'#EXTINF:-1 group-title="{ch["group"]}",{ch["name"]}\n')
            f.write(f'{ch["url"]}\n')

if __name__ == "__main__":
    start_time = time.time()
    final_data = fetch_and_process()
    save_m3u(final_data)
    end_time = time.time()
    print(f"\n🎉 处理完成！耗时: {int(end_time - start_time)}s，文件已更新至 tv.m3u")
