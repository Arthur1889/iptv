import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1. 10大热门直播源列表
SOURCE_URLS = [
    "https://live.fanmingming.com/tv/m3u/ipv6.m3u",
    "https://raw.githubusercontent.com/youshandefeiyang/IPTV/main/main.m3u",
    "https://raw.githubusercontent.com/Guovin/TV/gd/output/user_result.m3u",
    "https://iptv-org.github.io/iptv/countries/cn.m3u",
    "https://raw.githubusercontent.com/frankwuzp/iptv-cn/master/tv-ipv4-cn.m3u",
    "https://raw.githubusercontent.com/Gao-S/TVBox/main/v.m3u",
    "https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/grouped.m3u8",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn_cctv.m3u",
    "https://raw.githubusercontent.com/Tsing-Hua/IPTV/main/tv.m3u",
    "https://raw.githubusercontent.com/Moexin/IPTV/master/m3u/china.m3u"
]

def check_url(channel):
    """强制检测：只有2秒内能连通的才保留"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        # 严格检测：只取头部，超时设为2秒
        response = requests.get(channel['url'], headers=headers, timeout=2, stream=True)
        if response.status_code == 200:
            return channel
    except:
        pass
    return None

def get_group(name):
    name = name.upper()
    if "CCTV" in name or "央视" in name: return "央视频道"
    if "卫视" in name: return "地方卫视"
    return "其他频道"

def fetch_and_verify():
    raw_channels = []
    # 抓取阶段
    for url in SOURCE_URLS:
        try:
            print(f"抓取中: {url}")
            r = requests.get(url, timeout=10)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)\n', r.text, re.DOTALL)
            for name, link in matches:
                name, link = name.strip(), link.strip()
                # 过滤北京移动等内网源
                if "bj.chinamobile" not in link and "127.0.0.1" not in link:
                    raw_channels.append({"name": name, "url": link, "group": get_group(name)})
        except:
            continue

    # 去重
    unique_channels = {ch['url']: ch for ch in raw_channels}.values()
    print(f"初步发现 {len(unique_channels)} 个唯一链接，开始强制检测...")

    # 强制验证阶段 (使用50线程加速)
    valid_channels = []
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(check_url, ch) for ch in unique_channels]
        for future in as_completed(futures):
            res = future.result()
            if res:
                valid_channels.append(res)
    
    # 排序：央视 > 卫视 > 其他
    valid_channels.sort(key=lambda x: (x['group'] != '央视频道', x['group'] != '地方卫视', x['group']))
    return valid_channels

def save_m3u(channels):
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U x-tvg-url=\"https://live.fanmingming.com/e.xml\"\n")
        for ch in channels:
            f.write(f'#EXTINF:-1 tvg-name="{ch["name"]}" group-title="{ch["group"]}",{ch["name"]}\n')
            f.write(f'{ch["url"]}\n')

if __name__ == "__main__":
    final_list = fetch_and_verify()
    save_m3u(final_list)
    print(f"筛选完成！最终保留 {len(final_list)} 个优质频道。")
