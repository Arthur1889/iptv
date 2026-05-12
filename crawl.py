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
GROUP_PRIORITY = {"央视频道": 1, "地方卫视": 2, "上海频道": 3, "地方频道": 4, "其他频道": 5}

def get_quality_label(name):
    """根据原始名称识别分辨率标签"""
    name_up = name.upper()
    if any(w in name_up for w in ["4K", "8K", "UHD"]): return "4K"
    if any(w in name_up for w in ["1080", "FHD", "1080P"]): return "1080P"
    if any(w in name_up for w in ["720", "HD", "720P"]): return "720P"
    return ""

def clean_channel_name(name):
    """净化名称：只保留 频道名 + 分辨率"""
    res_label = get_quality_label(name)
    # 移除括号内容、多余标签、IPv6等杂质
    clean_name = re.sub(r'(\[.*?\]|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|B8|C7|A\d+)', '', name, flags=re.I)
    # 移除末尾连字符或空格
    clean_name = clean_name.split('-')[0].split('_')[0].strip()
    return f"{clean_name} {res_label}".strip() if res_label else clean_name

def get_quality_weight(name):
    """仅用于去重和排序时的权重参考，不再作为过滤条件"""
    name_up = name.upper()
    if any(w in name_up for w in ["4K", "8K", "UHD"]): return 1
    if any(w in name_up for w in ["1080", "FHD", "1080P"]): return 2
    if any(w in name_up for w in ["720", "HD", "720P"]): return 3
    if any(w in name_up for w in ["标清", "SD", "流畅"]): return 5
    return 4 # 默认画质

def extract_number(name):
    """序号排序逻辑"""
    nums = re.findall(r'\d+', name)
    if not nums: return 999
    val = float(nums[0])
    if '+' in name: val += 0.1
    return val

def get_group(name):
    """分组逻辑"""
    name_up = name.upper()
    if "CCTV" in name_up or "央视" in name: return "央视频道"
    if "卫视" in name: return "地方卫视"
    if any(s in name for s in ["上海", "东方", "五星体育", "新闻综合"]): return "上海频道"
    return "其他频道"

def check_url(channel):
    """验证连通性"""
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
                if "127.0.0.1" in link or "bj.chinamobile" in link: continue
                
                # 获取权重并净化名称
                q_weight = get_quality_weight(name)
                final_name = clean
