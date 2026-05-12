import requests
import re
import subprocess
import json
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

def deep_analyze_stream(url):
    """
    学习 iptv-org：使用 ffprobe 探测流的真实物理分辨率
    返回: (宽度, 高度, 编码格式)
    """
    cmd = [
        'ffprobe', '-v', 'quiet', '-print_format', 'json',
        '-show_streams', '-select_streams', 'v:0',
        '-timeout', '3000000', # 3秒探测超时
        url
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data.get('streams'):
                s = data['streams'][0]
                return s.get('width', 0), s.get('height', 0), s.get('codec_name', 'N/A')
    except:
        pass
    return 0, 0, None

def check_channel(ch):
    """综合验证：先测响应，再测流信息"""
    try:
        # 第一步：快速 HTTP 验证
        res = requests.get(ch['url'], timeout=3, stream=True)
        if res.status_code == 200:
            # 第二步：深度 FFprobe 验证（只针对连通的源）
            w, h, codec = deep_analyze_stream(ch['url'])
            if h > 0:
                # 根据真实物理高度标注画质
                if h >= 2160: label = "4K"
                elif h >= 1080: label = "1080P"
                elif h >= 720: label = "720P"
                else: label = "SD"
                
                ch['name'] = f"{ch['name']} [{label}]"
                ch['height'] = h  # 用于后续排序（分辨率高者优先）
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

    print(f">>> 开始深度分析 (预计耗时较长，候选源: {len(all_channels)})...")
    valid_channels = []
    # 深度分析耗性能，并发数不宜过高，建议 30-50
    with ThreadPoolExecutor(max_workers=40) as executor:
        futures = [executor.submit(check_channel, ch) for ch in all_channels]
        for f in as_completed(futures):
            res = f.result()
            if res:
                valid_channels.append(res)
                if len(valid_channels) % 10 == 0:
                    print(f"  已找到 {len(valid_channels)} 个真实有效源...")

    # 去重逻辑：同名同分辨率，保留一条
    unique_list = {}
    for ch in valid_channels:
        key = f"{ch['name']}_{ch['url'].split('/')[2] if '://' in ch['url'] else ''}"
        if key not in unique_list:
            unique_list[key] = ch
    
    # 最终排序：组别 > 分辨率高低 (height)
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
