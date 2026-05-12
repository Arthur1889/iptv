import os
import platform
import subprocess
import sys
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 1. 自动解决 Python 库依赖 =================
def ensure_dependencies():
    required_libs = ["requests", "tqdm"]
    for lib in required_libs:
        try:
            __import__(lib)
        except ImportError:
            print(f"📦 缺失库 {lib}，正在自动安装...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", lib])

ensure_dependencies()
import requests
from tqdm import tqdm

# ================= 2. 核心配置 =================
EPG_URL = "https://live.fanmingming.com/e.xml"
SOURCE_URLS = [
    "https://live.fanmingming.com/tv/m3u/ipv6.m3u",
    "https://iptv-org.github.io/iptv/countries/cn.m3u",
    "https://raw.githubusercontent.com/frankwuzp/iptv-cn/master/tv-ipv4-cn.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u",
    "https://raw.githubusercontent.com/plsy1/iptv/main/multicast/multicast-qingdao.m3u",
    "https://raw.githubusercontent.com/xcc360/SHCU-TV/refs/heads/main/IPTV.m3u",
    "https://raw.githubusercontent.com/babylife/China-ShangHai-IPTV-list/master/IPTV_Enhanced_change.m3u",
    "https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/cnTV_AutoUpdate.m3u8",
    "https://raw.githubusercontent.com/YueChan/IPTV/main/hongkong.m3u",
    "https://raw.githubusercontent.com/YueChan/IPTV/main/macau.m3u",
    "https://raw.githubusercontent.com/YueChan/IPTV/main/taiwan.m3u",
    "https://iptv-org.github.io/iptv/languages/eng.m3u",
    "https://raw.githubusercontent.com/LuenShor/IPTV/master/Global.m3u",
    "https://raw.githubusercontent.com/Guovin/TV/gd/output/result.m3u"
]

GROUP_PRIORITY = {
    "央视频道": 1, "地方卫视": 2, "上海频道": 3, "港澳台": 4, 
    "英文/国际": 5, "电影/影院": 6, "体育/竞技": 7, 
    "纪录/纪实": 8, "少儿/动画": 9, "综合/其他": 10
}

def get_env_config():
    sys_type = platform.system()
    config = {
        "os": sys_type,
        "ffprobe": "ffprobe",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    if sys_type == "Windows" and os.path.exists(r"C:\ffmpeg\bin\ffprobe.exe"):
        config["ffprobe"] = r"C:\ffmpeg\bin\ffprobe.exe"
    return config

ENV = get_env_config()

# ================= 3. 功能函数 =================
def clean_channel_name(name):
    name = re.sub(r'(\[.*?\]|【.*?】|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|B8|C7|A\d+|NOT 24/7|频道)', '', name, flags=re.I)
    name = name.replace("CCTV", "CCTV-")
    return re.sub(r'-+', '-', name).strip().rstrip('-').strip()

def get_group(name):
    n = name.upper()
    k_map = {
        "港澳台": ["翡翠", "TVB", "凤凰", "明珠", "J2", "HK", "澳门", "台湾", "年代", "中天", "纬来", "东森", "TVBS", "三立", "星空"],
        "英文/国际": ["BBC", "CNN", "HBO", "DISCOVERY", "NATIONAL", "MTV", "CNBC", "ANIMAL", "FOX", "BLOOMBERG", "DISNEY", "NICK"],
        "电影/影院": ["电影", "影院", "剧场", "影视频道", "CHC", "影视", "动作", "喜剧", "经典"],
        "体育/竞技": ["体育", "竞技", "足球", "篮球", "CCTV-5", "五星体育", "高尔夫", "网球", "极限", "赛事"],
        "纪录/纪实": ["纪录", "纪实", "探索", "人文", "地理", "世界", "历史"],
        "少儿/动画": ["少儿", "卡通", "动画", "金鹰", "卡酷", "炫动"]
    }
    if "CCTV" in n or "央视" in n: return "央视频道"
    if "卫视" in n: return "地方卫视"
    if any(s in n for s in ["上海", "东方", "五星体育", "新闻综合", "纪实人文"]): return "上海频道"
    for group, keys in k_map.items():
        if any(k in n for k in keys): return group
    return "综合/其他"

def deep_analyze_stream(url):
    """极致优化：限制探测大小和分析时长"""
    cmd = [
        ENV["ffprobe"], '-v', 'error', 
        '-probesize', '512000',      # 仅读 512KB
        '-analyzeduration', '1000000', # 分析 1s
        '-user_agent', ENV["ua"],
        '-show_entries', 'stream=width,height,bit_rate', 
        '-of', 'json', '-select_streams', 'v:0', '-timeout', '4000000', url # 4s 超时
    ]
    try:
        cf = subprocess.CREATE_NO_WINDOW if ENV["os"] == "Windows" else 0
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=8, creationflags=cf)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if 'streams' in data and data['streams']:
                s = data['streams'][0]
                return int(s.get('height', 0)), int(s.get('bit_rate', 0)) or 0
    except: pass
    return 0, 0

def check_channel(ch):
    """两级过滤：HEAD 请求初筛 + ffprobe 深筛"""
    headers = {"User-Agent": ENV["ua"]}
    try:
        # 第一级：1.5秒极速检测服务器状态
        res = requests.head(ch['url'], headers=headers, timeout=1.5, allow_redirects=True)
        if res.status_code == 200:
            # 第二级：深度分析
            h, br = deep_analyze_stream(ch['url'])
            if h > 0:
                ch['height'], ch['bitrate'] = h, br
                return ch, True
    except: pass
    return ch, False

# ================= 4. 主流程 =================
def fetch_and_process():
    all_channels = []
    seen_urls = set() # 预去重
    print(f"🚀 环境就绪 | 并发数: 30")
    
    # 步骤 1：获取源并预去重
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, headers={"User-Agent": ENV["ua"]}, timeout=8)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)(?:\n|$)', r.text)
            for name, link in matches:
                l_strip = link.strip()
                if l_strip not in seen_urls:
                    clean_n = clean_channel_name(name)
                    all_channels.append({"name": clean_n, "url": l_strip, "group": get_group(clean_n)})
                    seen_urls.add(l_strip)
        except: continue

    total = len(all_channels)
    print(f"[2/3] 🚀 正在筛选 (去重后总数: {total})...")
    
    best_channels = {}
    # 步骤 2：多线程深度筛选 (提升至 30 线程)
    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = {executor.submit(check_channel, ch): ch for ch in all_channels}
        with tqdm(total=total, desc="进度", bar_format='{l_bar}{bar:20}{r_bar}') as pbar:
            for f in as_completed(futures):
                res_ch, is_ok = f.result()
                if is_ok:
                    name = res_ch['name']
                    # 择优保存
                    if name not in best_channels or \
                       (res_ch['height'] > best_channels[name]['height']):
                        best_channels[name] = res_ch
                pbar.set_postfix({"有效": len(best_channels)})
                pbar.update(1)

    # 步骤 3：保存
    results = sorted(best_channels.values(), key=lambda x: (GROUP_PRIORITY.get(x['group'], 99), x['name']))
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write(f'#EXTM3U x-tvg-url="{EPG_URL}"\n')
        for ch in results:
            logo = f"https://live.fanmingming.com/tv/{ch['name'].replace('-', '')}.png"
            f.write(f'#EXTINF:-1 tvg-id="{ch["name"]}" tvg-logo="{logo}" group-title="{ch["group"]}",{ch["name"]}\n{ch["url"]}\n')
    
    return len(results)

if __name__ == "__main__":
    start = time.time()
    count = fetch_and_process()
    print(f"\n✨ 完成！筛选出 {count} 个频道。耗时: {int(time.time() - start)}s")