import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1. 确认后的 7 个源
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

def get_quality_info(name):
    name_up = name.upper()
    if any(w in name_up for w in ["4K", "8K", "UHD"]): return 1, "4K"
    if any(w in name_up for w in ["1080", "FHD", "1080P"]): return 2, "1080P"
    if any(w in name_up for w in ["720", "HD", "720P"]): return 3, "720P"
    return 4, "" 

def clean_channel_name(name):
    """优化后的净化：保留 CCTV-1 综合 这种具体名称"""
    _, res_label = get_quality_info(name)
    
    # 1. 先去掉常见的干扰标签
    clean = re.sub(r'(\[.*?\]|【.*?】|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|B8|C7|A\d+|NOT 24/7)', '', name, flags=re.I)
    
    # 2. 针对 CCTV 的特殊处理：保留 CCTV-1 这种格式及后面的中文
    # 移除末尾多余的空格和连字符
    clean = clean.strip().rstrip('-').strip()
    
    return f"{clean} {res_label}".strip() if res_label else clean

def extract_number(name):
    nums = re.findall(r'\d+', name)
    if not nums: return 999
    val = float(nums[0])
    if '+' in name: val += 0.1
    return val

def get_group(name):
    name_up = name.upper()
    if "CCTV" in name_up or "央视" in name: return "央视频道"
    if "卫视" in name: return "地方卫视"
    if any(s in name for s in ["上海", "东方", "五星体育", "新闻综合"]): return "上海频道"
    return "其他频道"

def check_url(channel):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(channel['url'], headers=headers, timeout=4, stream=True)
        if response.status_code == 200:
            channel['response_time'] = response.elapsed.total_seconds()
            return channel
    except: pass
    return None

def fetch_and_process():
    all_channels = []
    print("\n>>> 正在抓取...")
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, timeout=10)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)(?:\n|$)', r.text)
            
            if not matches: continue
            
            print(f"✅ 源: {url[:30]}... 抓取到 {len(matches)} 条")
            
            for name, link in matches:
                name, link = name.strip(), link.strip()
                if "127.0.0.1" in link or not link.startswith("http"): continue
                
                # 过滤明确的低画质
                if any(word in name.upper() for word in ["600P", "576I", "480P", "SD", "标清"]): continue

                q_weight, _ = get_quality_info(name)
                final_name = clean_channel_name(name)
                
                all_channels.append({
                    "name": final_name,
                    "url": link,
                    "group": get_group(name),
                    "quality_weight": q_weight
                })
        except: continue

    print(f"\n>>> 正在验证信号 (候选: {len(all_channels)})...")
    valid_channels = []
    with ThreadPoolExecutor(max_workers=60) as executor:
        futures = [executor.submit(check_url, ch) for ch in all_channels]
        for f in as_completed(futures):
            res = f.result()
            if res: valid_channels.append(res)

    print(f"\n>>> 正在精简去重 (保留多线路)...")
    best_channels = {}
    for ch in valid_channels:
        # 使用 名字+URL 作为唯一标识，确保不同线路的 CCTV-1 综合 都能共存
        key = f"{ch['name']}_{ch['url']}"
        best_channels[key] = ch
    
    final_list = list(best_channels.values())
    final_list.sort(key=lambda x: (GROUP_PRIORITY.get(x['group'], 99), extract_number(x['name']), x['quality_weight']))
    return final_list

def save_m3u(channels):
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U x-tvg-url=\"https://live.fanmingming.com/e.xml\"\n")
        for ch in channels:
            f.write(f'#EXTINF:-1 tvg-name="{ch["name"]}" group-title="{ch["group"]}",{ch["name"]}\n')
            f.write(f'{ch["url"]}\n')

if __name__ == "__main__":
    result = fetch_and_process()
    save_m3u(result)
    print(f"\n🎉 完成！已恢复 CCTV 详细名称，共保留 {len(result)} 个频道。")
