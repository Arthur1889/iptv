import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1. 仅保留你指定的 8 个源
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
    return 4, "" # 没写分辨率的统一给权重4，但不删除

def clean_channel_name(name):
    """净化标题：删掉 【Not 24/7】、括号内容等"""
    _, res_label = get_quality_info(name)
    # 正则：删除中英文方括号、圆括号及其内容
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
    print("\n>>> 步骤 1: 开始抓取与初步筛选")
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, timeout=8)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)\n', r.text, re.DOTALL)
            
            if not matches:
                continue
            
            print(f"✅ 抓取源: [{url[:40]}...] (发现 {len(matches)} 条)")
            
            for name, link in matches:
                name, link = name.strip(), link.strip()
                if "127.0.0.1" in link: continue
                
                name_up = name.upper()
                
                # --- 仅执行黑名单过滤 ---
                low_quality_keywords = ["600P", "576I", "480P", "标清", "SD", "流畅"]
                if any(word in name_up for word in low_quality_keywords):
                    continue # 明确写了低画质的才删掉
                
                # 识别画质和净化标题
                q_weight, _ = get_quality_info(name)
                final_name = clean_channel_name(name)
                
                all_channels.append({
                    "name": final_name,
                    "url": link,
                    "group": get_group(name),
                    "quality_weight": q_weight
                })
        except: continue

    print(f"\n>>> 步骤 2: 验证信号质量 (待验证: {len(all_channels)})")
    valid_channels = []
    count = 0
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(check_url, ch) for ch in all_channels]
        for f in as_completed(futures):
            res = f.result()
            count += 1
            if res: valid_channels.append(res)
            if count % 100 == 0: print(f"进度: {count}/{len(all_channels)}...")

    print(f"\n>>> 步骤 3: 同名去重与排序")
    best_channels = {}
    for ch in valid_channels:
        key = ch['name']
        # 相同名字，选画质权重高（数字小）的，如果画质一样则选响应快的
        if key not in best_channels or ch['quality_weight'] < best_channels[key]['quality_weight']:
            best_channels[key] = ch
        elif ch['quality_weight'] == best_channels[key]['quality_weight']:
            if ch['response_time'] < best_channels[key]['response_time']:
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
    print(f"\n🎉 搞定！已保留所有未明确标低画质的频道，总计 {len(result)} 个。")
