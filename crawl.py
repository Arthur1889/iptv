import requests
import re
from concurrent.futures import ThreadPoolExecutor

# 1. 强制保底源：这些是上海联通/电信环境非常稳定的高清直播源（不依赖验证，直接加入）
GUARANTEED_CHANNELS = [
    {"name": "CCTV-1综合", "url": "http://39.135.138.60:18890/PLTV/88888910/224/3221225618/index.m3u8", "group": "央视频道"},
    {"name": "CCTV-13新闻", "url": "http://39.134.115.163:18890/PLTV/88888910/224/3221225631/index.m3u8", "group": "央视频道"},
    {"name": "东方卫视", "url": "http://223.110.245.170/ott.js.chinamobile.com/PLTV/88888888/224/3221225822/index.m3u8", "group": "地方卫视"},
    {"name": "湖南卫视", "url": "http://223.110.245.159/ott.js.chinamobile.com/PLTV/88888888/224/3221225732/index.m3u8", "group": "地方卫视"}
]

# 2. 自动抓取源
SOURCE_URLS = [
    "https://live.fanmingming.com/tv/m3u/ipv6.m3u",
    "https://raw.githubusercontent.com/Guovin/TV/gd/output/user_result.m3u"
]

def check_url(channel):
    """验证逻辑：只针对非保底频道进行验证"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(channel['url'], headers=headers, timeout=2, stream=True)
        if response.status_code == 200:
            return channel
    except:
        pass
    return None

def get_group(name):
    if "CCTV" in name.upper() or "央视" in name: return "央视频道"
    if "卫视" in name: return "地方卫视"
    return "其他频道"

def fetch_and_filter():
    # 先把保底频道放进去
    final_list = list(GUARANTEED_CHANNELS)
    
    # 抓取外部源
    all_external = []
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, timeout=10)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)\n', r.text, re.DOTALL)
            for name, link in matches:
                if "bj.chinamobile" not in link:
                    all_external.append({"name": name.strip(), "url": link.strip(), "group": get_group(name)})
        except:
            continue

    # 验证外部源
    print(f"开始验证 {len(all_external)} 个外部频道...")
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = executor.map(check_url, all_external)
        for res in results:
            if res and res['url'] not in [c['url'] for c in final_list]:
                final_list.append(res)
    
    return final_list

def save_m3u(channels):
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U x-tvg-url=\"https://live.fanmingming.com/e.xml\"\n")
        for ch in channels:
            group = f' group-title="{ch["group"]}"'
            f.write(f'#EXTINF:-1 tvg-name="{ch["name"]}"{group},{ch["name"]}\n')
            f.write(f'{ch["url"]}\n')

if __name__ == "__main__":
    valid_list = fetch_and_filter()
    save_m3u(valid_list)
    print(f"完成！共计 {len(valid_list)} 个频道。")
