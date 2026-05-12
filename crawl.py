import requests
import re
import subprocess
import json
import time
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
    """基础净化：剔除杂质标签"""
    clean = re.sub(r'(\[.*?\]|【.*?】|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|B8|C7|A\d+|NOT 24/7)', '', name, flags=re.I)
    return clean.strip().rstrip('-').strip()

def get_group(name):
    name_up = name.upper()
    if "CCTV" in name_up or "央视" in name: return "央视频道"
    if "卫视" in name: return "地方卫视"
    if any(s in name for s in ["上海", "东方", "五星体育", "新闻综合"]): return "上海频道"
    return "其他频道"

def deep_analyze_stream(url, retry=1):
    """
    学习 iptv-org：使用 ffprobe 探测流的真实物理信息
    """
    cmd = [
        'ffprobe', 
        '-v', 'error', 
        '-show_entries', 'stream=width,height,codec_name', 
        '-of', 'json',
        '-select_streams', 'v:0',
        '-analyzeduration', '5000000', # 5秒分析时长
        '-probesize', '5000000',       # 5MB 探测包
        '-timeout', '5000000',          # 5秒超时
        url
    ]
    try:
        # 给 ffmpeg 15 秒总执行时间
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if 'streams' in data and len(data['streams']) > 0:
                s = data['streams'][0]
                return s.get('width', 0), s.get('height', 0), s.get('codec_name', 'N/A')
        
        # 如果第一次失败且还有重试次数
        if retry > 0:
            time.sleep(1)
            return deep_analyze_stream(url, retry - 1)
            
    except:
        pass
    return 0, 0, None

def check_channel(ch):
    """综合验证：先测 HTTP 响应，再测流内容"""
    try:
        # 使用快速请求确认服务器还活着
        res = requests.get(ch['url'], timeout=4, stream=True)
        if res.status_code == 200:
            # 只有 HTTP 通的源才进行昂贵的深度探测
            w, h, codec = deep_analyze_stream(ch['url'])
            if h > 0:
                if h >= 2160: label = "4K"
                elif h >= 1080: label = "1080P"
                elif h >= 720: label = "720P"
                else: label = "SD"
                
                ch['name'] = f"{ch['name']} [{label}]"
                ch['height'] = h
                return ch
    except:
        pass
    return None

def fetch_and_process():
    all_channels = []
    print(">>> 正在抓取源数据...")
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, timeout=10)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)(?:\n|$)', r.text)
            for name, link in matches:
                name, link = name.strip(), link.strip()
                if "127.0.0.1" in link: continue
                all_channels.append({
                    "name": clean_channel_name(name),
                    "url": link,
                    "group": get_group(name)
                })
        except: continue

    print(f">>> 开始深度分析 (候选源: {len(all_channels)})...")
    valid_channels = []
    
    # 核心修正：降低并发数到 15，防止网络拥塞导致 0 结果
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(check_channel, ch) for ch in all_channels]
        for f in as_completed(futures):
            res = f.result()
            if res:
                valid_channels.append(res)
                if len(valid_channels) % 5 == 0:
                    print(f"  已找到 {len(valid_channels)} 个高清有效源...")

    # 去重
    unique_list = {}
    for ch in valid_channels:
        domain = ch['url'].split('/')[2] if '://' in ch['url'] else 'default'
        key = f"{ch['name']}_{domain}"
        if key not in unique_list:
            unique_list[key] = ch
    
    results = list(unique_list.values())
    results.sort(key=lambda x: (GROUP_PRIORITY.get(x['group'], 99), -x.get('height', 0)))
    return results

def save_m3u(channels):
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in channels:
            f.write(f'#EXTINF:-1 group-title="{ch["group"]}",{ch["name"]}\n')
            f.write(f'{ch["url"]}\n')

if __name__ == "__main__":
    final_data = fetch_and_process()
    save_m3u(final_data)
    print(f"\n🎉 深度检测完成！生成了 {len(final_data)} 个真实可播频道。")
