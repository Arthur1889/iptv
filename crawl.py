import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1. 10大热门直播源列表 (涵盖央视、卫视及上海本地)
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
    "https://github.com/iptv-org/iptv/blob/master/streams/cn.m3u",
    "https://raw.githubusercontent.com/Moexin/IPTV/master/m3u/china.m3u"
]

# 2. 排序权重配置
GROUP_PRIORITY = {
    "央视频道": 1,
    "地方卫视": 2,
    "上海频道": 3,
    "地方频道": 4,
    "其他频道": 5
}

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
    """频道分组识别逻辑"""
    name_up = name.upper()
    if "CCTV" in name_up or "央视" in name:
        return "央视频道"
    elif "卫视" in name:
        return "地方卫视"
    elif any(s in name for s in ["上海", "东方", "五星体育", "新闻综合"]):
        return "上海频道"
    elif any(l in name for l in ["教育", "纪实", "都市", "哈哈", "七彩"]):
        return "地方频道"
    return "其他频道"

def fetch_and_verify():
    raw_channels = []
    # 抓取阶段
    for url in SOURCE_URLS:
        try:
            print(f"正在抓取源: {url}")
            r = requests.get(url, timeout=10)
            # 兼容标准 M3U 格式正则
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)\n', r.text, re.DOTALL)
            for name, link in matches:
                name, link = name.strip(), link.strip()
                # 过滤已知的内网源或无效地址
                if "bj.chinamobile" not in link and "127.0.0.1" not in link:
                    raw_channels.append({
                        "name": name, 
                        "url": link, 
                        "group": get_group(name)
                    })
        except Exception as e:
            print(f"抓取失败 {url}: {e}")
            continue

    # 去重
    unique_channels = {ch['url']: ch for ch in raw_channels}.values()
    print(f"发现 {len(unique_channels)} 个唯一链接，开始上海本地连通性强制检测...")

    # 强制验证阶段 (使用50线程加速)
    valid_channels = []
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(check_url, ch) for ch in unique_channels]
        for future in as_completed(futures):
            res = future.result()
            if res:
                valid_channels.append(res)
    
    # 排序逻辑：按 GROUP_PRIORITY 权重排，同组内按频道名排
    valid_channels.sort(key=lambda x: (
        GROUP_PRIORITY.get(x['group'], 99), 
        x['name']
    ))
    return valid_channels

def save_m3u(channels):
    """保存为标准 M3U 格式"""
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U x-tvg-url=\"https://live.fanmingming.com/e.xml\"\n")
        for ch in channels:
            # 写入 group-title 用于播放器分类
            f.write(f'#EXTINF:-1 tvg-name="{ch["name"]}" group-title="{ch["group"]}",{ch["name"]}\n')
            f.write(f'{ch["url"]}\n')

if __name__ == "__main__":
    final_list = fetch_and_verify()
    save_m3u(final_list)
    print(f"🎉 筛选完成！最终保留 {len(final_list)} 个优质频道。")
