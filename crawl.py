import os
import platform
import subprocess
import sys
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 1. 环境与依赖 =================
def ensure_dependencies():
    required_libs = ["requests", "tqdm"]
    for lib in required_libs:
        try:
            __import__(lib)
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", lib])

ensure_dependencies()
import requests
from tqdm import tqdm

def get_env_config():
    sys_type = platform.system()
    config = {
        "os": sys_type,
        "ffprobe": "ffprobe",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "timeout": 3, # 统一超时设置
        "workers": 50 # 并发数
    }
    if sys_type == "Windows" and os.path.exists(r"C:\ffmpeg\bin\ffprobe.exe"):
        config["ffprobe"] = r"C:\ffmpeg\bin\ffprobe.exe"
    return config

ENV = get_env_config()

# ================= 2. 核心配置 =================
SOURCE_URLS = [
    "https://live.fanmingming.com/tv/m3u/ipv6.m3u",
    "https://iptv-org.github.io/iptv/countries/cn.m3u",
    "https://raw.githubusercontent.com/frankwuzp/iptv-cn/master/tv-ipv4-cn.m3u",
    "https://raw.githubusercontent.com/xcc360/SHCU-TV/refs/heads/main/IPTV.m3u",
    "https://raw.githubusercontent.com/babylife/China-ShangHai-IPTV-list/master/IPTV_Enhanced_change.m3u",
    "https://raw.githubusercontent.com/YueChan/IPTV/main/hongkong.m3u",
    "https://raw.githubusercontent.com/YueChan/IPTV/main/macau.m3u",
    "https://raw.githubusercontent.com/YueChan/IPTV/main/taiwan.m3u",
    "https://raw.githubusercontent.com/Guovin/TV/gd/output/result.m3u"
]

GROUP_PRIORITY = {
    "央视频道": 1, "地方卫视": 2, "上海频道": 3, "港澳台": 4, 
    "电影/影院": 5, "体育/竞技": 6, "英文/国际": 7, "纪录/纪实": 8, "少儿/动画": 9
}

# ================= 3. 功能函数 =================
def clean_channel_name(name):
    name = re.sub(r'(\[.*?\]|【.*?】|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|频道|画质)', '', name, flags=re.I)
    name = name.replace("CCTV", "CCTV-").replace("CCTV--", "CCTV-")
    return name.strip().upper()

def get_group(name):
    n = name.upper()
    if "CCTV" in n: return "央视频道"
    if "卫视" in n: return "地方卫视"
    if any(s in n for s in ["上海", "东方", "五星体育", "新闻综合", "纪实人文"]): return "上海频道"
    k_map = {
        "港澳台": ["翡翠", "TVB", "凤凰", "明珠", "J2", "HK", "澳门", "台湾", "年代", "中天", "纬来", "东森", "TVBS", "三立", "星空"],
        "电影/影院": ["电影", "影院", "剧场", "影视", "CHC", "动作", "喜剧", "经典"],
        "体育/竞技": ["体育", "竞技", "足球", "篮球", "高尔夫", "网球", "极限", "赛事"],
        "英文/国际": ["BBC", "CNN", "HBO", "DISCOVERY", "NATIONAL", "MTV", "CNBC", "ANIMAL", "FOX", "BLOOMBERG"],
        "纪录/纪实": ["纪录", "纪实", "探索", "人文", "地理", "世界", "历史"],
        "少儿/动画": ["少儿", "卡通", "动画", "金鹰", "卡酷", "炫动"]
    }
    for group, keys in k_map.items():
        if any(k in n for k in keys): return group
    return "综合/其他"

def deep_analyze_stream(url):
    """画质优化：精简分析时长，读取码率"""
    cmd = [
        ENV["ffprobe"], '-v', 'error', 
        '-probesize', '256000',       # 减小探测包，极大提升速度
        '-analyzeduration', '500000',  # 0.5s 分析
        '-user_agent', ENV["ua"],
        '-show_entries', 'stream=width,height,bit_rate', 
        '-of', 'json', '-select_streams', 'v:0', 
        '-timeout', '3000000', url     # 3s 超时
    ]
    try:
        cf = subprocess.CREATE_NO_WINDOW if ENV["os"] == "Windows" else 0
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, creationflags=cf)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if 'streams' in data and data['streams']:
                s = data['streams'][0]
                return int(s.get('height', 0)), int(s.get('bit_rate', 0)) or 0
    except: pass
    return 0, 0

def check_channel(ch, session):
    """速度优化：利用 Session 复用连接"""
    try:
        res = session.head(ch['url'], timeout=ENV["timeout"], allow_redirects=True)
        if res.status_code == 200:
            h, br = deep_analyze_stream(ch['url'])
            if h >= 360: # 过滤掉极低画质源
                ch['height'], ch['bitrate'] = h, br
                return ch, True
    except: pass
    return ch, False

def sort_key(ch):
    """逻辑优化：精准排序（CCTV数字补位 + 画质权重）"""
    group_p = GROUP_PRIORITY.get(ch['group'], 99)
    name = ch['name']
    
    # CCTV 数字排序修复：将 CCTV-1 转换为 CCTV-01
    cctv_match = re.search(r'CCTV-(\d+)', name)
    cctv_num = int(cctv_match.group(1)) if cctv_match else 999
    
    # 画质权重：分辨率越高清越排前，同分辨率比码率
    quality_score = ch.get('height', 0) * 10000000 + ch.get('bitrate', 0)
    
    return (group_p, cctv_num, name, -quality_score)

# ================= 4. 主流程 =================
def run():
    all_channels = []
    seen_urls = set()
    session = requests.Session()
    session.headers.update({"User-Agent": ENV["ua"]})

    print(f"📡 正在获取节目源...")
    for url in SOURCE_URLS:
        try:
            r = session.get(url, timeout=10)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)(?:\n|$)', r.text)
            for name, link in matches:
                link = link.strip()
                if link not in seen_urls:
                    clean_n = clean_channel_name(name)
                    all_channels.append({"name": clean_n, "url": link, "group": get_group(clean_n)})
                    seen_urls.add(link)
        except Exception as e:
            print(f"⚠️ 无法访问源: {url[:30]}...")

    print(f"🚀 深度筛选开始 (线程数: {ENV['workers']})...")
    best_channels = {} # 格式: {name: channel_info}

    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(check_channel, ch, session): ch for ch in all_channels}
        for f in tqdm(as_completed(futures), total=len(all_channels), desc="探测进度", bar_format='{l_bar}{bar:20}{r_bar}'):
            res_ch, is_ok = f.result()
            if is_ok:
                name = res_ch['name']
                # 画质择优逻辑：
                # 如果频道不存在，或者当前源的分辨率更高，或者分辨率相同但码率更高，则更新
                if name not in best_channels:
                    best_channels[name] = res_ch
                else:
                    curr_best = best_channels[name]
                    if (res_ch['height'] > curr_best['height']) or \
                       (res_ch['height'] == curr_best['height'] and res_ch['bitrate'] > curr_best['bitrate']):
                        best_channels[name] = res_ch

    # 排序与保存
    final_list = sorted(best_channels.values(), key=sort_key)
    
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            logo = f"https://live.fanmingming.com/tv/{ch['name'].replace('-', '')}.png"
            f.write(f'#EXTINF:-1 tvg-id="{ch["name"]}" tvg-logo="{logo}" group-title="{ch["group"]}",{ch["name"]}\n{ch["url"]}\n')

    print(f"\n✅ 完成！筛选出 {len(final_list)} 个最优频道源。结果已保存至 tv.m3u")

if __name__ == "__main__":
    start_time = time.time()
    run()
    print(f"⏳ 总耗时: {int(time.time() - start_time)} 秒")