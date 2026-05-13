import os
import platform
import subprocess
import sys
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 0. 禁用 SSL 警告 =================
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
        "timeout": 3, 
        "workers": 60 
    }
    if sys_type == "Windows" and os.path.exists(r"C:\ffmpeg\bin\ffprobe.exe"):
        config["ffprobe"] = r"C:\ffmpeg\bin\ffprobe.exe"
    return config

ENV = get_env_config()

# ================= 2. 核心配置 =================
CONFIG_FILE = "sources.json"

def load_sources():
    """从本地 JSON 文件读取源链接"""
    if not os.path.exists(CONFIG_FILE):
        print(f"❌ 错误：找不到配置文件 {CONFIG_FILE}！")
        return []
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("urls", [])
    except Exception as e:
        print(f"❌ 读取配置文件出错: {e}")
        return []

SOURCE_URLS = load_sources()

GROUP_PRIORITY = {
    "央视频道": 1, "地方卫视": 2, "上海频道": 3, "港澳台": 4, 
    "电影/影院": 5, "体育/竞技": 6, "英文/国际": 7, "纪录/纪实": 8, "少儿/动画": 9 
}

# ================= 3. 功能函数 =================

def clean_channel_name(name, height=0):
    """
    清洗频道名并物理识别 4K
    """
    name = re.sub(r'(\[.*?\]|【.*?】|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|频道|画质)', '', name, flags=re.I)
    name = name.replace("CCTV", "CCTV-").replace("CCTV--", "CCTV-")
    name = name.strip().upper()
    
    # 4K 物理识别：如果探测高度 >= 2160，强制标注
    if height >= 2160:
        if "4K" not in name:
            name = f"{name}-4K"
    return name

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
    cmd = [
        ENV["ffprobe"], '-v', 'error', '-probesize', '256000', 
        '-analyzeduration', '500000', '-user_agent', ENV["ua"], 
        '-show_entries', 'stream=width,height,bit_rate', '-of', 'json', 
        '-select_streams', 'v:0', '-timeout', '3000000', url 
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
    try:
        res = session.head(ch['url'], timeout=ENV["timeout"], allow_redirects=True)
        if res.status_code == 200:
            h, br = deep_analyze_stream(ch['url'])
            if h >= 360:
                ch['height'], ch['bitrate'] = h, br
                # 在探测后根据分辨率重新修正名字（打上 4K 标签）
                ch['name'] = clean_channel_name(ch['name'], height=h)
                return ch, True
    except: pass
    return ch, False

def sort_key(ch):
    group_p = GROUP_PRIORITY.get(ch['group'], 99)
    name = ch['name']
    cctv_match = re.search(r'CCTV-(\d+)', name)
    cctv_num = int(cctv_match.group(1)) if cctv_match else 999
    quality_score = ch.get('height', 0) * 10000000 + ch.get('bitrate', 0)
    return (group_p, cctv_num, name, -quality_score)

# ================= 4. 主流程 =================
def run():
    if not SOURCE_URLS:
        print("⚠️ 没有可用的源链接，请检查 sources.json！")
        return

    all_channels = []
    seen_urls = set()
    source_stats = {}
    session = requests.Session()
    session.headers.update({"User-Agent": ENV["ua"]})

    print(f"\n📡 [1/3] 正在从 {len(SOURCE_URLS)} 个源提取链接...")
    for url in SOURCE_URLS:
        try:
            r = session.get(url, timeout=12, verify=False)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)(?:\n|$)', r.text)
            count_before = len(seen_urls)
            for name, link in matches:
                link = link.strip()
                if link not in seen_urls:
                    clean_n = clean_channel_name(name)
                    all_channels.append({"name": clean_n, "url": link, "group": get_group(clean_n)})
                    seen_urls.add(link)
            source_stats[url.split('/')[-1]] = len(seen_urls) - count_before
        except:
            print(f"  ❌ 无法连接: {url[:50]}...")

    print(f"\n🚀 [2/3] 原始链接总数: {len(all_channels)} | 开始探测 (线程: {ENV['workers']})...")
    
    # 核心优化：允许 4K 和高清版共存
    best_channels = {}
    valid_count = 0

    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(check_channel, ch, session): ch for ch in all_channels}
        for f in tqdm(as_completed(futures), total=len(all_channels), desc="进度", bar_format='{l_bar}{bar:20}{r_bar}'):
            res_ch, is_ok = f.result()
            if is_ok:
                valid_count += 1
                # 使用 名字+分辨率 作为 key，确保 4K 不会被同名高清源覆盖
                unique_key = f"{res_ch['name']}_{res_ch['height']}"
                
                if unique_key not in best_channels:
                    best_channels[unique_key] = res_ch
                else:
                    curr = best_channels[unique_key]
                    if res_ch['bitrate'] > curr['bitrate']:
                        best_channels[unique_key] = res_ch

    final_list = sorted(best_channels.values(), key=sort_key)

    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            # Logo 适配：4K 频道也使用普通台标
            logo_id = ch['name'].replace('-4K', '').replace('-', '')
            logo = f"https://live.fanmingming.com/tv/{logo_id}.png"
            f.write(f'#EXTINF:-1 tvg-id="{ch["name"]}" tvg-logo="{logo}" group-title="{ch["group"]}",{ch["name"]}\n{ch["url"]}\n')

    print("\n" + "="*50)
    print("📊 运行摘要报告")
    print("-" * 50)
    print(f"✅ 探测存活源总数:  {valid_count}")
    print(f"💎 精选去重后频道:  {len(final_list)}")
    print(f"📦 结果已存入文件:  tv.m3u")
    print("\n各源唯一链接贡献排行 (Top 5):")
    sorted_stats = sorted(source_stats.items(), key=lambda x: x[1], reverse=True)
    for src, count in sorted_stats[:5]:
        print(f"  - {src}: {count} 条")
    print("="*50 + "\n")

if __name__ == "__main__":
    start_time = time.time()
    run()
    print(f"⏱️ 总耗时: {int(time.time() - start_time)} 秒")