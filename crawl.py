import os
import sys
import json
import re
import time
import platform
import logging
import urllib.request
import subprocess
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 配置常量 =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_JSON = os.path.join(BASE_DIR, 'sources.json')
CACHE_FILE = os.path.join(BASE_DIR, 'sources_cache.txt')
NAME_JSON = os.path.join(BASE_DIR, 'name.json')
GROUP_JSON = os.path.join(BASE_DIR, 'group.json')
BLACKLIST_JSON = os.path.join(BASE_DIR, 'blacklist.json')
OUTPUT_M3U = os.path.join(BASE_DIR, 'tv.m3u')
LOG_FILE = os.path.join(BASE_DIR, 'iptv_task.log')

# 分组排序规则 (第5步)
GROUP_ORDER = [
    "4K频道", "央视频道", "地方卫视", "山东频道", "地方频道", "影视频道", 
    "歌曲及音乐MV", "纪录纪实", "娱乐频道", "电视剧直播", "动漫直播", 
    "港澳台", "海外频道", "体育赛事", "少儿频道", "综合频道"
]

# 央视后缀映射
CCTV_MAP = {
    "CCTV1": "CCTV-1 综合", "CCTV2": "CCTV-2 财经", "CCTV3": "CCTV-3 综艺",
    "CCTV4": "CCTV-4 中文国际", "CCTV5": "CCTV-5 体育", "CCTV5+": "CCTV-5+ 体育赛事",
    "CCTV6": "CCTV-6 电影", "CCTV7": "CCTV-7 国防军事", "CCTV8": "CCTV-8 电视剧",
    "CCTV9": "CCTV-9 纪录", "CCTV10": "CCTV-10 科教", "CCTV11": "CCTV-11 戏曲",
    "CCTV12": "CCTV-12 社会与法", "CCTV13": "CCTV-13 新闻", "CCTV14": "CCTV-14 少儿",
    "CCTV15": "CCTV-15 音乐", "CCTV16": "CCTV-16 奥林匹克", "CCTV17": "CCTV-17 农业农村",
    "CCTV4K": "CCTV4K 超高清", "CCTV8K": "CCTV8K 超高清",
    "CETV1": "CETV1:中国教育-1", "CETV2": "CETV2:中国教育-2",
    "CETV3": "CETV3:中国教育-3", "CETV4": "CETV4:中国教育-4"
}

# 垃圾分组直接抛弃
DROP_GROUPS = [
    "🎮游戏直播", "📚听书直播", "👴🏻老年直播", "🔊解说直播", "👁️‍🗨️监控直播",
    "🏀蜘蛛直播", "🏀zuqiu直播", "🏀[三网1]咪视界直播", "🏀[三网2]咪视界直播",
    "🏀[移动]咪视界直播", "👠KK直播", "🧘🏻‍♀️瑜伽裤直播", "🤖Ai直播", "🎣钓鱼直播", "🎰API随机点播"
]

# ================= 日志系统 =================
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.FileHandler(LOG_FILE, 'a', 'utf-8')])
logger = logging.getLogger(__name__)

# ================= 核心处理函数 =================

def get_os_env():
    """判断系统环境"""
    sys_os = platform.system()
    logger.info(f"当前系统环境: {sys_os}")
    return sys_os

def load_json(path, default=None):
    if not os.path.exists(path): return default or {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def fetch_sources_to_cache():
    """第一步：提取并缓存（每天1次）"""
    if os.path.exists(CACHE_FILE):
        mtime = datetime.fromtimestamp(os.path.getmtime(CACHE_FILE))
        if datetime.now() - mtime < timedelta(days=1):
            logger.info("缓存文件在24小时内，跳过抓取，直接使用缓存。")
            return
    
    sources = load_json(SOURCES_JSON, [])
    if not sources: return
    
    logger.info("开始从 sources.json 拉取源列表到缓存...")
    all_lines = set()
    for url in sources:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read().decode('utf-8').splitlines()
                # 简单合并去重
                current_extinf = ""
                for line in content:
                    line = line.strip()
                    if line.startswith("#EXTINF"):
                        current_extinf = line
                    elif line.startswith("http") and current_extinf:
                        all_lines.add(f"{current_extinf}\n{line}")
                        current_extinf = ""
        except Exception as e:
            logger.error(f"拉取源 {url} 失败: {e}")
            
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        for item in all_lines:
            f.write(item + "\n")
    logger.info(f"缓存写入完成，共计 {len(all_lines)} 个独立频道源。")

def clean_channel_name(name):
    """规则6：去除无用后缀"""
    patterns = [r'(?i)360P', r'(?i)404P', r'(?i)480P', r'(?i)576P', r'(?i)606P', 
                r'(?i)720P', r'(?i)1080P', r'(?i)HD', r'(?i)Not 24/7', r'(?i)Geo-blocked']
    for p in patterns:
        name = re.sub(p, '', name)
    return name.strip()

def categorize_group(std_name, original_group, groups_db):
    """分组规则：根据优先级映射"""
    # 优先匹配 group.json
    for g_name, channels in groups_db.items():
        if std_name in channels:
            return g_name

    # 后备分组映射
    if original_group in DROP_GROUPS: return "DROP"
    if "🌐地方台直播" in original_group: return "地方频道"
    if "🔮港澳台直播" in original_group: return "港澳台"
    if original_group in ["🇲🇾马来西亚直播", "🇻🇳越南直播", "🇮🇳印度直播", "🇯🇵日本直播", 
                          "🇰🇷韩国直播", "🇺🇸美国直播", "🇬🇧英国直播", "🇮🇪爱尔兰直播", "🌏全球直播"]: 
        return "海外频道"
    if "👶🏻少儿直播" in original_group: return "少儿频道"
    if original_group in ["🏀[国内]体育直播", "🏀[海外]体育直播"]: return "体育赛事"
    if "🎞️电影直播" in original_group: return "影视频道"
    if original_group in ["📽️综艺直播", "🍿短剧直播", "🧨小品直播", "🎚️相声直播", "🎙️抖音直播", 
                          "🤟🏻YY直播", "💋车模直播", "💄女团直播", "💃🏻热舞直播", "🌲乡野直播", "🗣️脱口秀直播"]: 
        return "娱乐频道"
    if original_group in ["📺电视剧直播", "💖爱奇艺直播", "🎨埋堆堆直播"]: return "电视剧直播"
    if "💽纪录片直播" in original_group: return "纪录纪实"
    if original_group in ["🎎动漫直播", "🤣沙雕动画直播"]: return "动漫直播"
    if original_group in ["🎧音乐直播", "🎤周杰伦歌曲点播", "🎹歌手合集点播"]: return "歌曲及音乐MV"
    
    return "综合频道"

def probe_resolution(url):
    """使用 ffprobe 探测分辨率"""
    cmd = [
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=height', '-of', 'csv=p=0', url
    ]
    try:
        # 设置5秒超时，防止死链卡死
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        if res.returncode == 0 and res.stdout.strip().isdigit():
            return int(res.stdout.strip())
    except Exception:
        pass
    return -1

def print_progress(processed, total, start_time, valid_count):
    """规则1：单行进度条不刷屏"""
    elapsed = time.time() - start_time
    speed = processed / elapsed if elapsed > 0 else 0
    percent = (processed / total) * 100
    bar_len = 30
    filled = int(bar_len * processed // total)
    bar = '█' * filled + '-' * (bar_len - filled)
    
    sys.stdout.write(f"\r🚀 探测进度: [{bar}] {percent:.1f}% | 耗时:{elapsed:.0f}s | 速度:{speed:.1f}源/s | 优质源:{valid_count}/{total}    ")
    sys.stdout.flush()

def main():
    start_time = time.time()
    get_os_env()
    fetch_sources_to_cache()
    
    name_db = load_json(NAME_JSON)
    group_db = load_json(GROUP_JSON)
    blacklist = load_json(BLACKLIST_JSON)
    
    channels_dict = {}
    total_parsed = 0
    blacklist_filtered = 0
    keyword_filtered = 0

    # ================= 第二/三步：解析、过滤与标准化 =================
    if not os.path.exists(CACHE_FILE):
        print("未找到缓存文件，程序退出。")
        return

    logger.info("开始解析合并后的缓存文件...")
    with open(CACHE_FILE, 'r', encoding='utf-8') as f:
        lines = f.read().splitlines()
    
    tasks = []
    for i in range(len(lines)):
        if lines[i].startswith("#EXTINF"):
            extinf = lines[i]
            url = lines[i+1] if i+1 < len(lines) and lines[i+1].startswith("http") else ""
            if not url: continue
            
            total_parsed += 1
            
            # 规则4/过滤：黑名单拦截
            if blacklist.get(url, 0) >= 3:
                blacklist_filtered += 1
                continue
                
            # 规则3：过滤特定关键词
            if "catvod.com" in url or "直播间" in extinf:
                keyword_filtered += 1
                continue
            
            # 解析 M3U 标签
            tvg_id = re.search(r'tvg-id="([^"]+)"', extinf)
            tvg_name = re.search(r'tvg-name="([^"]+)"', extinf)
            tvg_logo = re.search(r'tvg-logo="([^"]+)"', extinf)
            group_title = re.search(r'group-title="([^"]+)"', extinf)
            
            tvg_id = tvg_id.group(1) if tvg_id else ""
            tvg_name = tvg_name.group(1) if tvg_name else ""
            tvg_logo = tvg_logo.group(1) if tvg_logo else ""
            orig_group = group_title.group(1) if group_title else ""
            display_name = extinf.split(',')[-1].strip() if ',' in extinf else ""

            # 第3步：匹配 name.json
            std_name = clean_channel_name(display_name)
            for key, aliases in name_db.items():
                if (clean_channel_name(tvg_id) in aliases or 
                    clean_channel_name(tvg_name) in aliases or 
                    std_name in aliases):
                    std_name = key
                    break
            
            # 规则8/9：保留原有4K/8K标识，或者 CCTV4K 特殊处理
            if "4K" in display_name.upper() and "4K" not in std_name.upper(): std_name += " 4K"
            if "8K" in display_name.upper() and "8K" not in std_name.upper(): std_name += " 8K"
            
            # 分组指派
            target_group = categorize_group(std_name, orig_group, group_db)
            if target_group == "DROP": continue
            
            tasks.append({
                'url': url,
                'std_name': std_name,
                'tvg_logo': tvg_logo,
                'group': target_group
            })

    # ================= 第四步：多线程并发探测 (FFprobe) =================
    logger.info(f"解析完成。进入探测池源数量: {len(tasks)}")
    print("\n") # 为进度条留出空行
    
    valid_count = 0
    processed_count = 0
    
    # 构建数据结构进行去重与保留策略
    # struct: { std_name: { '4k': [], 'hd': [], 'sd': [] } }
    grouped_channels = {}
    
    with ThreadPoolExecutor(max_workers=50) as executor:
        future_to_task = {executor.submit(probe_resolution, t['url']): t for t in tasks}
        
        for future in as_completed(future_to_task):
            t = future_to_task[future]
            processed_count += 1
            height = future.result()
            
            if height == -1: # 探测失败
                blacklist[t['url']] = blacklist.get(t['url'], 0) + 1
            else:
                blacklist[t['url']] = 0 # 成功则清零失败计数
                
                # 记录不同分辨率类别
                cat = 'sd'
                if height >= 2160: cat = '4k'
                elif height >= 1080: cat = 'hd'
                elif height >= 720: cat = 'hd_low' # 特别为央视/卫视准备的 720p 档
                
                name_key = t['std_name']
                if name_key not in grouped_channels:
                    grouped_channels[name_key] = {'4k':[], 'hd':[], 'hd_low':[], 'sd':[]}
                
                grouped_channels[name_key][cat].append({'url': t['url'], 'height': height, 'logo': t['tvg_logo'], 'group': t['group']})
                valid_count += 1
            
            print_progress(processed_count, len(tasks), start_time, valid_count)
            
    print("\n") # 进度条结束换行
    save_json(BLACKLIST_JSON, blacklist)

    # ================= 第五步：去重、降级规则与分组重定义 =================
    final_playlist = []
    quality_filtered = 0
    
    for name, cats in grouped_channels.items():
        # 按分辨率降序排列
        for c in cats.values(): c.sort(key=lambda x: x['height'], reverse=True)
        
        # 规则4：4K及以上保留一个，4K以下保留一个
        best_4k = cats['4k'][0] if cats['4k'] else None
        
        # 挑选 < 4K 的最佳源 (优先 >=1080p)
        best_sub4k = cats['hd'][0] if cats['hd'] else None
        
        # 规则2：如果不满1080p，如果是央视/卫视，允许保留 720p 或以下至少一个
        base_group = (best_4k['group'] if best_4k else (cats['hd'][0]['group'] if cats['hd'] else 
                     (cats['hd_low'][0]['group'] if cats['hd_low'] else 
                     (cats['sd'][0]['group'] if cats['sd'] else ""))))
                     
        if not best_sub4k and base_group in ["央视频道", "地方卫视"]:
            best_sub4k = cats['hd_low'][0] if cats['hd_low'] else (cats['sd'][0] if cats['sd'] else None)
        else:
            # 不是央视卫视且没有 HD 以上，直接丢弃
            if not best_sub4k and not best_4k:
                quality_filtered += sum(len(x) for x in cats.values())

        # 添加到最终列表，并执行规则7：4K频道的重分组
        if best_4k:
            if base_group in ["央视频道", "地方卫视", "地方频道"]:
                best_4k['group'] = "4K频道"
            final_playlist.append({**best_4k, 'name': name})
            
        if best_sub4k:
            final_playlist.append({**best_sub4k, 'name': name})
            
    # ================= 排序逻辑 =================
    def sort_key(item):
        grp_idx = GROUP_ORDER.index(item['group']) if item['group'] in GROUP_ORDER else 999
        cctv_idx = 999
        if item['name'].startswith('CCTV'):
            m = re.search(r'CCTV-?(\d+)', item['name'])
            if m: cctv_idx = int(m.group(1))
        return (grp_idx, cctv_idx, item['name'])

    final_playlist.sort(key=sort_key)

    # ================= 第六步：生成 tv.m3u =================
    logger.info("开始生成 tv.m3u ...")
    with open(OUTPUT_M3U, 'w', encoding='utf-8') as f:
        # 规则12/13：备用 EPG 注入
        f.write('#EXTM3U x-tvg-url="https://epg.112114.xyz/"\n')
        
        for item in final_playlist:
            raw_name = item['name']
            
            # 应用 CCTV 中文描述映射
            display_name = CCTV_MAP.get(raw_name, raw_name)
            
            # 标准 M3U8 格式组装
            extinf = f'#EXTINF:-1 tvg-id="{raw_name}" tvg-name="{raw_name}" tvg-logo="{item["logo"]}" group-title="{item["group"]}", {display_name}'
            f.write(f'{extinf}\n{item["url"]}\n')
            
    # ================= 任务报告 =================
    total_time = time.time() - start_time
    report = f"""
====================================
           📺 IPTV 任务报告
====================================
🔹 初始源梳理数量: {total_parsed}
🔹 黑名单过滤数量: {blacklist_filtered}
🔹 关键词过滤数量: {keyword_filtered}
🔹 低画质淘汰数量: {quality_filtered}
✅ 最终保留可用源: {len(final_playlist)}
⏱️ 探测及处理耗时: {total_time:.2f} 秒
====================================
"""
    print(report)
    logger.info(report.replace('\n', ' '))

if __name__ == "__main__":
    main()