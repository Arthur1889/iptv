import requests
import re
from concurrent.futures import ThreadPoolExecutor

# 1. 精选源列表：包含央视、卫视及高清源
SOURCE_URLS = [
    "https://live.fanmingming.com/tv/m3u/ipv6.m3u",
    "https://raw.githubusercontent.com/youshandefeiyang/IPTV/main/main.m3u"
]

def check_url(channel):
    """验证链接有效性（2秒超时）"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(channel['url'], headers=headers, timeout=2, stream=True)
        if response.status_code == 200:
            return channel
    except:
        pass
    return None

def get_group(name):
    """根据频道名自动分组"""
    if "CCTV" in name.upper():
        return "央视频道"
    elif "卫视" in name:
        return "地方卫视"
    return "其他频道"

def fetch_and_filter():
    all_channels = []
    for url in SOURCE_URLS:
        try:
            print(f"正在抓取源: {url}")
            r = requests.get(url, timeout=10)
            # 兼容更多样式的 M3U 格式
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)\n', r.text, re.DOTALL)
            for name, link in matches:
                name = name.strip()
                link = link.strip()
                # 过滤掉已知的北京移动内网源
                if "bj.chinamobile" not in link:
                    all_channels.append({
                        "name": name,
                        "url": link,
                        "group": get_group(name)
                    })
        except:
            print(f"抓取 {url} 失败")

    # 去重：以 URL 为唯一标识
    unique_channels = {ch['url']: ch for ch in all_channels}.values()
    print(f"去重后共 {len(unique_channels)} 个频道，开始验证...")

    valid_channels = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = executor.map(check_url, unique_channels)
        for res in results:
            if res:
                valid_channels.append(res)
    
    return valid_channels

def save_m3u(channels):
    with open("tv.m3u", "w", encoding="utf-8") as f:
        # 增加节目单(EPG)地址，方便 Apple TV 显示节目预告
        f.write("#EXTM3U x-tvg-url=\"https://live.fanmingming.com/e.xml\"\n")
        for ch in channels:
            # 只有 央视频道 和 地方卫视 才会显示分组名，其他不分组
            group_info = f' group-title="{ch["group"]}"' if ch["group"] != "其他频道" else ""
            f.write(f'#EXTINF:-1 tvg-name="{ch["name"]}"{group_info},{ch["name"]}\n')
            f.write(f'{ch["url"]}\n')

if __name__ == "__main__":
    valid_list = fetch_and_filter()
    save_m3u(valid_list)
    print(f"同步完成！最终保留 {len(valid_list)} 个经过验证的频道")
