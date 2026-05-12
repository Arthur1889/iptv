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
    """确保运行所需的 Python 第三方库已安装"""
    required_libs = ["requests", "tqdm"]
    for lib in required_libs:
        try:
            __import__(lib)
        except ImportError:
            print(f"📦 发现缺失 Python 库: {lib}，正在尝试自动安装...")
            try:
                # 使用当前 Python 解释器运行 pip 安装
                subprocess.check_call([sys.executable, "-m", "pip", "install", lib])
                print(f"✅ {lib} 安装成功！")
            except Exception as e:
                print(f"❌ 自动安装失败，请手动在终端运行: pip install {lib}")
                sys.exit(1)

# 执行依赖检查
ensure_dependencies()

# 依赖检查通过后导入
import requests
from tqdm import tqdm

# ================= 2. 核心配置与环境适配 =================
EPG_URL = "https://live.fanmingming.com/e.xml"
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

def get_env_config():
    """检测系统平台并配置路径"""
    sys_type = platform.system()
    config = {
        "os": sys_type,
        "ffprobe": "ffprobe", # 默认直接调用环境变量
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    if sys_type == "Windows":
        # Windows 优先尝试常见的自定义安装路径
        win_path = r"C:\ffmpeg\bin\ffprobe.exe"
        if os.path.exists(win_path):
            config["ffprobe"] = win_path
    
    # 验证 ffprobe 是否可用
    try:
        subprocess.run([config["ffprobe"], "-version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"\n❌ 错误: 未检测到系统依赖 FFmpeg (ffprobe)")
        if sys_type == "Darwin":
            print("💡 Mac 用户请运行: brew install ffmpeg")
        elif sys_type == "Windows":
            print("💡 Windows 用户请下载 FFmpeg 并将其 bin 目录加入环境变量，或放入 C:\\ffmpeg\\bin")
        sys.exit(1)
        
    return config

ENV = get_env_config()

# ================= 3. 功能函数 =================
def clean_channel_name(name):
    name = re.sub(r'(\[.*?\]|【.*?】|\(.*?\)|\d+K|蓝光|超清|高清|标清|FHD|HD|SD|IP[vV]6|IPV4|B8|C7|A\d+|NOT 24/7|频道)', '', name, flags=re.I)
    name = name.replace("CCTV", "CCTV-")
    name = re.sub(r'-+', '-', name).strip().rstrip('-').strip()
    return name

def get_group(name):
    n = name.upper()
    if "CCTV" in n or "央视" in n: return "央视频道"
    if "卫视" in n: return "地方卫视"
    if any(s in n for s in ["上海", "东方", "五星体育", "新闻综合", "纪实人文"]): return "上海频道"
    return "其他频道"

def sort_key(ch):
    group_p = GROUP_PRIORITY.get(ch['group'], 99)
    name = ch['name'].upper()
    cctv_num = 999
    if "CCTV-" in name:
        match = re.search(r'CCTV-(\d+)', name)
        if match: cctv_num = int(match.group(1))
        elif "5+" in name: cctv_num = 5.5
    return (group_p, cctv_num, -ch.get('height', 0))

def deep_analyze_stream(url):
    """探测视频流分辨率和码率"""
    cmd = [
        ENV["ffprobe"], '-v', 'error', 
        '-user_agent', ENV["ua"], # 必须带上 UA，否则很多源会返回 0 分辨率
        '-show_entries', 'stream=width,height,bit_rate', 
        '-of', 'json', '-select_streams', 'v:0', '-timeout', '5000000', url
    ]
    try:
        # Windows 运行脚本时隐藏黑窗口弹窗
        creation_flags = subprocess.CREATE_NO_WINDOW if ENV["os"] == "Windows" else 0
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, creationflags=creation_flags)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if 'streams' in data and data['streams']:
                s = data['streams'][0]
                return int(s.get('height', 0)), int(s.get('bit_rate', 0)) if s.get('bit_rate') else 0
    except: pass
    return 0, 0

def check_channel(ch):
    """多级检查：HTTP 连通性 + 深度分析"""
    headers = {"User-Agent": ENV["ua"]}
    try:
        # 先用 HEAD 请求快速判断，若被禁(405/403)则回退到 GET
        res = requests.head(ch['url'], headers=headers, timeout=3, allow_redirects=True)
        if res.status_code >= 400:
            res = requests.get(ch['url'], headers=headers, timeout=3, stream=True)
            
        if res.status_code < 400:
            h, br = deep_analyze_stream(ch['url'])
            if h > 0:
                ch['height'], ch['bitrate'] = h, br
                return ch, True
    except: pass
    return ch, False

# ================= 4. 主流程 =================
def fetch_and_process():
    all_channels = []
    print(f"🚀 环境就绪 | 平台: {ENV['os']} | 探针: {ENV['ffprobe']}")
    print("[1/3] 🔍 正在获取源列表...")
    
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, headers={"User-Agent": ENV["ua"]}, timeout=10)
            matches = re.findall(r'#EXTINF:.*?,(.*?)\n(http.*?)(?:\n|$)', r.text)
            for name, link in matches:
                clean_n = clean_channel_name(name)
                all_channels.append({"name": clean_n, "url": link.strip(), "group": get_group(clean_n)})
        except: continue

    total = len(all_channels)
    print(f"[2/3] 🚀 深度分析中 (总数: {total})...")
    
    best_channels = {}
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(check_channel, ch): ch for ch in all_channels}
        with tqdm(total=total, desc="分析进度", bar_format='{l_bar}{bar:30}{r_bar}') as pbar:
            for f in as_completed(futures):
                res_ch, is_ok = f.result()
                if is_ok:
                    name = res_ch['name']
                    # 分辨率优先，码率次之
                    if name not in best_channels or \
                       (res_ch['height'] > best_channels[name]['height']) or \
                       (res_ch['height'] == best_channels[name]['height'] and res_ch['bitrate'] > best_channels[name]['bitrate']):
                        best_channels[name] = res_ch
                pbar.set_postfix({"有效": len(best_channels)})
                pbar.update(1)

    print(f"\n[3/3] 💾 正在保存结果...")
    results = list(best_channels.values())
    results.sort(key=sort_key)
    
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write(f'#EXTM3U x-tvg-url="{EPG_URL}"\n')
        for ch in results:
            logo = f"https://live.fanmingming.com/tv/{ch['name'].replace('-', '')}.png"
            f.write(f'#EXTINF:-1 tvg-id="{ch["name"]}" tvg-name="{ch["name"]}" tvg-logo="{logo}" group-title="{ch["group"]}",{ch["name"]}\n')
            f.write(f'{ch["url"]}\n')
    
    return len(results)

if __name__ == "__main__":
    start_time = time.time()
    count = fetch_and_process()
    print(f"\n✨ 任务完成！共筛选出 {count} 个优质频道。")
    print(f"⏱️ 总耗时: {int(time.time() - start_time)}s")