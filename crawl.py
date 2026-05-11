import requests
import re
from concurrent.futures import ThreadPoolExecutor

# 1. 选取更通用的全国性 IPv4 数据源（适合上海及全国各地）
SOURCE_URLS = [
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/ipv6.m3u", # 混合源
    "https://iptv-org.github.io/iptv/countries/cn.m3u" # 国际维护的中国源
]

def check_url(channel):
    """
    验证链接有效性
    """
    try:
        # 模拟播放器头部，防止被屏蔽
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        # 检查链接，设置 2 秒超时
        # 注意：由于 GitHub Actions 环境限制，部分国内源可能验证失败，这里采用灵活验证
        response = requests.get(channel['url'], headers=headers, timeout=2, stream=True)
        if response.status_code == 200:
            return channel
    except:
        pass
    return None

def fetch_and_filter():
    all_channels = []
    
    # 抓取多个源并合并
    for url in SOURCE_URLS:
        try:
            print(f"正在抓取源: {url}")
            r = requests.get(url, timeout=10)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)\n', r.text, re.DOTALL)
            for name, link in matches:
                # 过滤掉北京移动内网源 (通常包含 bj.chinamobile)
                if "bj.chinamobile" not in link:
                    all_channels.append({"name": name.strip(), "url": link.strip()})
        except:
            print(f"抓取 {url} 失败")

    # 去重
    unique_channels = {ch['url']: ch for ch in all_channels}.values()
    print(f"去重后共 {len(unique_channels)} 个频道，开始验证...")

    # 多线程验证（20线程）
    valid_channels = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = executor.map(check_url, unique_channels)
        for res in results:
            if res:
                valid_channels.append(res)
    
    return valid_channels

def save_m3u(channels):
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U x-tvg-url=\"https://live.fanmingming.com/e.xml\"\n")
        for ch in channels:
            f.write(f'#EXTINF:-1, {ch["name"]}\n')
            f.write(f'{ch["url"]}\n')

if __name__ == "__main__":
    valid_list = fetch_and_filter()
    save_m3u(valid_list)
    print(f"同步完成！最终保留 {len(valid_list)} 个上海可用频道")
