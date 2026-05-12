import os
import platform
import subprocess
import re
import requests
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor

# ================= 配置区域 =================
# 自动识别系统并选择 ffprobe 路径
def get_ffprobe_path():
    sys_type = platform.system()
    if sys_type == "Windows":
        # 如果你 Windows 的路径不同，请在此修改
        return r"C:\ffmpeg\bin\ffprobe.exe"
    else:
        # Mac 或 Linux 直接调用环境变量
        return "ffprobe"

FFPROBE_PATH = get_ffprobe_path()

# 数据源链接（可以添加更多）
SOURCE_URLS = [
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/ipv6.m3u",
    "https://raw.githubusercontent.com/Guovin/TV/gd/output/result.m3u"
]

# 探测配置
TIMEOUT = 10  # 每个链接探测的超时时间（秒）
MAX_WORKERS = 10  # 并行线程数，数值越大速度越快，但容易封IP
# ============================================

def probe_quality(url):
    """使用 ffprobe 探测视频流真实分辨率和码率"""
    cmd = [
        FFPROBE_PATH,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,bit_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        url
    ]
    try:
        # 运行探测命令
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        if result.returncode == 0 and result.stdout.strip():
            # 输出格式通常为: width \n height \n bitrate
            info = result.stdout.strip().split('\n')
            width = int(info[0]) if len(info) > 0 else 0
            height = int(info[1]) if len(info) > 1 else 0
            return width * height  # 返回像素总量作为质量评分
    except Exception:
        pass
    return 0

def main():
    print(f"[{platform.system()}] 运行环境检测成功，使用探针: {FFPROBE_PATH}")
    print("[1/3] 🔍 正在从各数据源抓取频道列表...")
    
    all_channels = {} # 格式: { "CCTV-1": [url1, url2], ... }
    
    for url in SOURCE_URLS:
        try:
            response = requests.get(url, timeout=10)
            lines = response.text.split('\n')
            current_name = ""
            for line in lines:
                if "tvg-name=" in line or "EXTINF" in line:
                    # 匹配频道名称
                    name_match = re.search(r'tvg-name="([^"]+)"', line) or re.search(r',(.+)$', line)
                    if name_match:
                        current_name = name_match.group(1).strip()
                elif line.startswith("http"):
                    if current_name and line.strip():
                        if current_name not in all_channels:
                            all_channels[current_name] = []
                        all_channels[current_name].append(line.strip())
        except Exception as e:
            print(f"⚠️ 抓取源 {url} 失败: {e}")

    print(f"[2/3] 🚀 开始深度分析与择优 (共 {len(all_channels)} 个频道)...")
    
    best_results = []

    # 内部探测函数
    def process_channel(name_urls):
        name, urls = name_urls
        best_url = None
        max_score = 0
        
        for url in urls:
            score = probe_quality(url)
            if score > max_score:
                max_score = score
                best_url = url
        
        if best_url:
            return f'#EXTINF:-1 tvg-name="{name}" tvg-logo="https://live.fanmingming.com/tv/{name}.png" group-title="自动择优",{name}\n{best_url}'
        return None

    # 使用线程池加速
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(tqdm(executor.map(process_channel, all_channels.items()), total=len(all_channels), desc="分析进度"))

    # 过滤空结果并排序（简单按名称排序）
    final_list = [r for r in results if r]
    final_list.sort()

    print("\n[3/3] 💾 正在保存结果...")
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write("\n".join(final_list))

    print(f"\n✨ 任务完成！")
    print(f"统计：抓取到 {len(final_list)} 个优质频道")
    print(f"结果已保存至: tv.m3u")

if __name__ == "__main__":
    main()