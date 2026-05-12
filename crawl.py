import requests
import re
import subprocess
import json
import time
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1. 确认后的 7 个高权重源
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

def clean_channel_name(name):
    """剔除杂质标签"""
    clean = re.sub(r'(\[.*?\]|【.*?】|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|B8|C7|A\d+|NOT 24/7)', '', name, flags=re.I)
    return clean.strip().rstrip('-').strip()

def get_group(name):
    """频道分组逻辑"""
    name_up = name.upper()
    if "CCTV" in name_up or "央视" in name: return "央视频道"
    if "卫视" in name: return "地方卫视"
    if any(s in name for s in ["上海", "东方", "五星体育", "新闻综合"]): return "上海频道"
    return "其他频道"

def deep_analyze_stream(url):
    """使用 ffprobe 探测流信息 (自动适配 Windows/Linux)"""
    # 路径自适应：优先找你本地 C 盘，找不到则使用系统变量（针对 GitHub Actions）
    win_path = r"C:\ffmpeg\bin\ffprobe.exe"
    ffprobe_path = win_path if os.path.exists(win_path) else "ffprobe"
    
    cmd = [
        ffprobe_path, '-v', 'error', '-show_entries', 'stream=width,height', 
        '-of', 'json', '-select_streams', 'v:0',
        '-analyzeduration', '5000000', '-probesize', '5000000',
        '-timeout', '5000000', url
    ]
    try:
        # 如果是 Windows 且路径存在，添加不弹窗标志
        extra_args = {}
        if os.name == 'nt' and os.path.exists(win_path):
            extra_args['creationflags'] = subprocess.CREATE_NO_WINDOW
            
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, **extra_args)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if 'streams' in data and len(data['streams']) > 0:
                s = data['streams'][0]
                return s.get('width', 0), s.get('height', 0)
    except:
        pass
    return 0, 0

def check_channel(ch):
    """综合验证逻辑"""
    try:
        # 第一步：快速 HTTP
