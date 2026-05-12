import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# 保持不变的 12 个源
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

GROUP_PRIORITY = {"央视频道": 1, "地方卫视": 2, "上海频道": 3, "地方频道": 4, "其他频道": 5}

def get_quality_info(name):
    """识别分辨率并返回权重和标签"""
    name_up = name.upper()
    if any(w in name_up for w in ["4K", "8K", "UHD"]): return 1, "4K"
    if any(w in name_up for w in ["1080", "FHD", "1080P"]): return 2, "1080P"
    if any(w in name_up for w in ["720", "HD", "720P"]): return 3, "720P"
    # 明确标注标清或无标注的，返回 None 用于过滤
    return None, ""

def clean_channel_name(name):
    """
    极简净化：删除【Not 24/7】、IPv6、括号等一切杂质
    """
    # 1. 识别并提取分辨率标签
    _, res_label = get_quality_info(name)
    
    # 2. 移除所有干扰项：包括【...】、(...)、IPv6、蓝光、超清、高清等
    clean_name = re.sub(r'(\[.*?\]|【.*?】|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|B8|C7|A\d+)', '', name, flags=re.I)
    
    # 3. 移除末尾多余符号并去空格
    clean_name = clean_name.split('-')[0].split('_')[0].strip()
    
    # 4. 拼接最终在 VLC 显示的标题
    return f"{clean_name} {res_label}".strip() if res_label else clean_name

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
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, timeout=10)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)\n', r.text, re.DOTALL)
            for name, link in matches:
                name, link = name.strip(), link.strip()
                
                # --- 规则1：画质过滤 (720P以下不要) ---
                q_weight, res_label = get_quality_info(name)
                if q_weight is None:
                    # 针对 CCTV 和 卫视 做豁免，即便没标分辨率也保留（权重设为 4）
                    if "CCTV" in name.upper() or "卫视" in name:
                        q_weight = 4
                    else:
                        continue 
                
                # --- 规则2：名称净化 (删掉【Not 24/7】等) ---
                final_display_name = clean_channel_name(name)
                
                all_channels.append({
                    "name": final_display_name,
                    "url": link,
                    "group": get_group(name),
                    "quality_weight": q_weight
                })
        except: continue

    print(f"正在验证信号质量，当前候选频道数: {len(all_channels)}")

    valid_channels = []
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(check_url, ch) for ch in all_channels]
        for f in as_completed(futures):
            res = f.result()
            if res: valid_channels.append(res)

    # --- 规则3：基于净化后的名称去重 ---
    best_channels = {}
    for ch in valid_channels:
        key = ch['name']
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
            # 这里的字段决定了 VLC 里显示的标题
            f.write(f'#EXTINF:-1 tvg-name="{ch["name"]}" group-title="{ch["group"]}",{ch["name"]}\n')
            f.write(f'{ch["url"]}\n')

if __name__ == "__main__":
    result = fetch_and_process()
    save_m3u(result)
    print(f"🎉 处理完成！VLC 标题已净化，720P 以下已剔除。共计 {len(result)} 个频道。")
