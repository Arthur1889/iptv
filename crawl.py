import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1. 保持不变的 12 个源列表
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
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u",
    "https://raw.githubusercontent.com/Moexin/IPTV/master/m3u/china.m3u",
    "https://raw.githubusercontent.com/babylife/China-ShangHai-IPTV-list/master/IPTV_Enhanced_change.m3u"
]

# 2. 排序权重配置
GROUP_PRIORITY = {
    "央视频道": 1,
    "地方卫视": 2,
    "上海频道": 3,
    "地方频道": 4,
    "其他频道": 5
}

def get_quality_weight(name):
    """根据名称关键词评估画质权重，数值越小优先级越高"""
    name_up = name.upper()
    if any(word in name_up for word in ["4K", "8K", "蓝光", "BD", "超高清"]):
        return 1
    if any(word in name_up for word in ["1080", "超清", "HD", "FHD"]):
        return 2
    if any(word in name_up for word in ["720", "高清"]):
        return 3
    if any(word in name_up for word in ["标清", "SD", "流畅"]):
        return 5
    return 4

def extract_number(name):
    """从频道名提取数字（如 CCTV-1 -> 1, CCTV-5+ -> 5.1）用于精确排序"""
    nums = re.findall(r'\d+', name)
    if not nums:
        return 999
    val = float(nums[0])
    if '+' in name:
        val += 0.1
    return val

def get_group(name):
    """频道分组识别"""
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

def check_url(channel):
    """验证连通性并记录响应时间"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(channel['url'], headers=headers, timeout=2, stream=True)
        if response.status_code == 200:
            channel['response_time'] = response.elapsed.total_seconds()
            return channel
    except:
        pass
    return None

def fetch_and_process():
    all_channels = []
    for url in SOURCE_URLS:
        try:
            print(f"抓取源: {url}")
            r = requests.get(url, timeout=10)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)\n', r.text, re.DOTALL)
            for name, link in matches:
                name, link = name.strip(), link.strip()
                if "127.0.0.1" not in link and "bj.chinamobile" not in link:
                    all_channels.append({
                        "name": name,
                        "url": link,
                        "group": get_group(name),
                        "quality_weight": get_quality_weight(name)
                    })
        except:
            continue

    print(f"开始本地筛选与去重，原始链接数: {len(all_channels)}")

    valid_channels = []
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(check_url, ch) for ch in all_channels]
        for future in as_completed(futures):
            res = future.result()
            if res:
                valid_channels.append(res)

    # 去重逻辑：同名频道保留 (画质权重最高 > 响应时间最短) 的那一个
    best_channels = {}
    for ch in valid_channels:
        name = ch['name']
        if name not in best_channels:
            best_channels[name] = ch
        else:
            # 比较画质权重（越小越好）
            if ch['quality_weight'] < best_channels[name]['quality_weight']:
                best_channels[name] = ch
            # 权重相同时，比较响应时间（越快越好）
            elif ch['quality_weight'] == best_channels[name]['quality_weight']:
                if ch['response_time'] < best_channels[name]['response_time']:
                    best_channels[name] = ch
    
    final_list = list(best_channels.values())

    # 排序：组权重 > 序号 > 画质权重
    final_list.sort(key=lambda x: (
        GROUP_PRIORITY.get(x['group'], 99),
        extract_number(x['name']),
        x['quality_weight'],
        x['name']
    ))
    
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
    print(f"🎉 处理完成！保留 {len(result)} 个高画质频道。")
