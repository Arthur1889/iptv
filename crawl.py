import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1. 按照要求删除了 iptv-org 的央视专用源，保留其余 7 个源
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
    """识别分辨率权重和标签"""
    name_up = name.upper()
    if any(w in name_up for w in ["4K", "8K", "UHD"]): return 1, "4K"
    if any(w in name_up for w in ["1080", "FHD", "1080P"]): return 2, "1080P"
    if any(w in name_up for w in ["720", "HD", "720P"]): return 3, "720P"
    return 4, "" 

def clean_channel_name(name):
    """净化标题：剔除杂质，但保留核心名"""
    _, res_label = get_quality_info(name)
    # 正则：删除括号内容、画质标识、IPv6等
    clean = re.sub(r'(\[.*?\]|【.*?】|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|B8|C7|A\d+)', '', name, flags=re.I)
    # 仅保留核心频道名
    clean = re.split(r'[-_ ]', clean)[0].strip()
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
        response = requests.get(channel['url'], headers=headers, timeout=3, stream=True)
        if response.status_code == 200:
            channel['response_time'] = response.elapsed.total_seconds()
            return channel
    except: pass
    return None

def fetch_and_process():
    all_channels = []
    print("\n--- 步骤 1: 开始从 7 个源抓取数据 ---")
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, timeout=8)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)\n', r.text, re.DOTALL)
            
            if not matches: continue
            
            print(f"✅ 抓取源: [{url[:40]}...] (发现 {len(matches)} 条)")
            
            for name, link in matches:
                name, link = name.strip(), link.strip()
                if "127.0.0.1" in link: continue
                
                # 过滤明确的低画质
                name_up = name.upper()
                low_quality = ["600P", "576I", "480P", "标清", "SD", "流畅"]
                if any(word in name_up for word in low_quality):
                    continue

                q_weight, _ = get_quality_info(name)
                final_name = clean_channel_name(name)
                
                all_channels.append({
                    "name": final_name,
                    "url": link,
                    "group": get_group(name),
                    "quality_weight": q_weight
                })
        except: continue

    print(f"\n--- 步骤 2: 验证信号 (候选: {len(all_channels)}) ---")
    valid_channels = []
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(check_url, ch) for ch in all_channels]
        for f in as_completed(futures):
            res = f.result()
            if res: valid_channels.append(res)

    print(f"\n--- 步骤 3: 优化去重 (保留多线路) ---")
    best_channels = {}
    for ch in valid_channels:
        # 【核心改进】: Key = 名字 + 域名(或URL前部分)
        # 这样 CCTV1 如果在 A 源和 B 源都有，都会被保留，不会被合并。
        url_domain = ch['url'].split('/')[2] if "://" in ch['url'] else ch['url'][:10]
        key = f"{ch['name']}_{url_domain}"
        
        if key not in best_channels:
            best_channels[key] = ch
        else:
            # 只有
