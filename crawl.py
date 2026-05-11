import requests
import re
from concurrent.futures import ThreadPoolExecutor

# 1. 更加稳定且包含央视的源（针对国内环境优化）
SOURCE_URLS = [
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/ipv6.m3u",
    "https://raw.githubusercontent.com/Guovin/TV/gd/output/user_result.m3u", # 包含大量央视和卫视
    "https://raw.githubusercontent.com/youshandefeiyang/IPTV/main/main.m3u"
]

def check_url(channel):
    """
    针对央视源放宽验证条件，或者对已知稳定的域名跳过验证
    """
    # 如果是常见的央视稳定域名，直接返回，不走验证（防止被 GitHub 墙掉）
    stable_keywords = ["cctv", "yangshipin", "cgtn"]
    if any(k in channel['url'].lower() for k in stable_keywords):
        return channel
        
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        # 验证时长稍微放宽到 3 秒
        response = requests.get(channel['url'], headers=headers, timeout=3, stream=True)
        if response.status_code == 200:
            return channel
    except:
        pass
    return None

def get_group(name):
    name_up = name.upper()
    if "CCTV" in name_up or "央视" in name:
        return "央视频道"
    elif "卫视" in name:
        return "地方卫视"
    elif "上海" in name or "东方" in name:
        return "上海频道"
    return "其他频道"

def fetch_and_filter():
    all_channels = []
    for url in SOURCE_URLS:
        try:
            print(f"正在从 {url} 抓取...")
            r = requests.get(url, timeout=15)
            # 改进正则，兼容更多 M3U 变体
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)\n', r.text, re.DOTALL)
            for name, link in matches:
                name = name.strip()
                link = link.strip()
                # 排除已知的北京移动/特定地区拨号源
                if "bj.chinamobile" not in link and "127.0.0.1" not in link:
                    all_channels.append({
                        "name": name,
                        "url": link,
                        "group": get_group(name)
                    })
        except:
            continue

    # 去重
    unique_channels = {ch['url']: ch for ch in all_channels}.values()
    print(f"初步获得 {len(unique_channels)} 个频道，开始筛选...")

    valid_channels = []
    with ThreadPoolExecutor(max_workers=30) as executor:
        results = executor.map(check_url, unique_channels)
        for res in results:
            if res:
                valid_channels.append(res)
    
    # 按照分组排序，让央视排在最前面
    valid_channels.sort(key=lambda x: (x['group'] != '央视频道', x['group'] != '地方卫视', x['group']))
    return valid_channels

def save_m3u(channels):
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U x-tvg-url=\"https://live.fanmingming.com/e.xml\"\n")
        for ch in channels:
            group_str = f' group-title="{ch["group"]}"'
            # 增加 tvg-id 方便 APTV 匹配图标
            f.write(f'#EXTINF:-1 tvg-name="{ch["name"]}"{group_str},{ch["name"]}\n')
            f.write(f'{ch["url"]}\n')

if __name__ == "__main__":
    final_list = fetch_and_filter()
    save_m3u(final_list)
    print(f"更新成功！包含央视/卫视共 {len(final_list)} 个频道")
