import os
import sys
import json
import re
import time
import platform
import logging
import urllib.request
import ssl
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

GROUP_ORDER = [
    "4K频道", "央视频道", "地方卫视", "山东频道", "地方频道", "影视频道", 
    "歌曲及音乐MV", "纪录纪实", "娱乐频道", "电视剧直播", "动漫直播", 
    "港澳台", "海外频道", "体育赛事", "少儿频道", "综合频道"
]

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

# ================= 核心工具函数 =================
def load_json(path, default=None):
    if not os.path.exists(path): return default or {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def fetch_sources_to_cache():
    """第一步：拉取源到缓存去重"""
    logger.info(f"当前系统环境: {platform.system()}")
    if os.path.exists(CACHE_FILE):
        mtime = datetime.fromtimestamp(os.path.getmtime(CACHE_FILE))
        if datetime.now() - mtime < timedelta(days=1):
            logger.info("缓存文件在24小时内，使用本地缓存。")
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

def clean_channel_name(name):
    """规则6：过滤指定画质和状态后缀"""
    patterns = [r'(?i)360P', r'(?i)404P', r'(?i)480P', r'(?i)576P', r'(?i)606P', 
                r'(?i)720P', r'(?i)1080P', r'(?i)HD', r'(?i)Not\s*24/7', r'(?i)Geo-blocked']
    for p in patterns:
        name = re.sub(p, '', name)
    return name.strip()

def categorize_group(std_name, original_group, groups_db):
    """分组规则与 14 条回退法则"""
    # 优先匹配 group.json
    for g_name, channels in groups_db.items():
        if std_name in channels: return g_name

    # 回退法则 (Rules 1-14)
    if original_group in DROP_GROUPS: return "DROP"
    if "🌐地方台直播" in original_group: return "地方频道"
    if "🔮港澳台直播" in original_group: return "港澳台"
    if original_group in ["🇲🇾马来西亚直播", "🇻🇳越南直播", "🇮🇳印度直播", "🇯🇵日本直播", 
                          "🇰🇷韩国直播", "🇺🇸美国直播", "🇬🇧英国直播", "🇮🇪爱尔兰直播", "🌏全球直播"]: return "海外频道"
    if "👶🏻少儿直播" in original_group: return "少儿频道"
    if original_group in ["🏀[国内]体育直播", "🏀[海外]体育直播"]: return "体育赛事"
    if "🎞️电影直播" in original_group: return "影视频道"
    if original_group in ["📽️综艺直播", "🍿短剧直播", "🧨小品直播", "🎚️相声直播", "🎙️抖音直播", 
                          "🤟🏻YY直播", "💋车模直播", "💄女团直播", "💃🏻热舞直播", "🌲乡野直播", "🗣️脱口秀直播"]: return "娱乐频道"
    if original_group in ["📺电视剧直播", "💖爱奇艺直播", "🎨埋堆堆直播"]: return "电视剧直播"
    if "💽纪录片直播" in original_group: return "纪录纪实"
    if original_group in ["🎎动漫直播", "🤣沙雕动画直播"]: return "动漫直播"
    if original_group in ["🎧音乐直播", "🎤周杰伦歌曲点播", "🎹歌手合集点播"]: return "歌曲及音乐MV"
    
    return "综合频道"

def probe_url_and_guess_quality(url, original_name):
    """高效宽容探测：仅看连通性，忽略 SSL 错误，盲猜画质以防止误杀"""
    # 忽略过期/无效的 SSL 证书
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        # 伪装成普通浏览器
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*'
        }
        req = urllib.request.Request(url, headers=headers)
        
        # 5秒极速测活
        with urllib.request.urlopen(req, timeout=5, context=ctx) as response:
            status = response.getcode()
            if status == 200 or status == 302:
                # 连通成功，盲猜画质
                name_upper = original_name.upper()
                if '8K' in name_upper: return 4320
                if '4K' in name_upper or '2160' in name_upper: return 2160
                if '1080' in name_upper or 'FHD' in name_upper: return 1080
                if '720' in name_upper or 'HD' in name_upper: return 720
                if '标清' in name_upper or 'SD' in name_upper: return 480
                
                # 无明确标识的，统一“无罪推定”为 1080P，防止被画质过滤规则丢弃
                return 1080 
    except Exception:
        return -1
    return -1

def print_progress(processed, total, start_time, valid_count):
    """规则1/11：单行不刷屏进度条"""
    elapsed = time.time() - start_time
    speed = processed / elapsed if elapsed > 0 else 0
    percent = (processed / total) * 100
    bar = '█' * int(30 * processed // total) + '-' * (30 - int(30 * processed // total))
    sys.stdout.write(f"\r🚀 探测进度: [{bar}] {percent:.1f}% | 耗时:{elapsed:.0f}s | 速度:{speed:.1f}源/s | 优质源:{valid_count}/{total}    ")
    sys.stdout.flush()

# ================= 主流程 =================
def main():
    start_time = time.time()
    fetch_sources_to_cache()
    
    name_db = load_json(NAME_JSON)
    group_db = load_json(GROUP_JSON)
    blacklist = load_json(BLACKLIST_JSON)
    
    if not os.path.exists(CACHE_FILE):
        print("❌ 未找到缓存文件，程序退出。")
        return

    stats = {'total': 0, 'bl_drop': 0, 'kw_drop': 0, 'quality_drop': 0}
    tasks = []

    # 第二/三步：解析清洗
    with open(CACHE_FILE, 'r', encoding='utf-8') as f: lines = f.read().splitlines()
    for i in range(len(lines)):
        if lines[i].startswith("#EXTINF"):
            extinf = lines[i]
            url = lines[i+1] if i+1 < len(lines) and lines[i+1].startswith("http") else ""
            if not url: continue
            stats['total'] += 1
            
            # 规则4/过滤：黑名单拦截 (兼容旧版数据格式)
            fail_count = blacklist.get(url, 0)
            if not isinstance(fail_count, int): fail_count = 0
            if fail_count >= 3:
                stats['bl_drop'] += 1
                continue
                
            # 规则3：关键词过滤
            if "catvod.com" in url or "直播间" in extinf:
                stats['kw_drop'] += 1
                continue
            
            tvg_id = (re.search(r'tvg-id="([^"]+)"', extinf) or [None, ""])[1]
            tvg_name = (re.search(r'tvg-name="([^"]+)"', extinf) or [None, ""])[1]
            tvg_logo = (re.search(r'tvg-logo="([^"]+)"', extinf) or [None, ""])[1]
            orig_group = (re.search(r'group-title="([^"]+)"', extinf) or [None, ""])[1]
            display_name = extinf.split(',')[-1].strip() if ',' in extinf else ""

            # 名称清洗与映射
            std_name = clean_channel_name(display_name)
            for key, aliases in name_db.items():
                if clean_channel_name(tvg_id) in aliases or clean_channel_name(tvg_name) in aliases or std_name in aliases:
                    std_name = key
                    break
            
            target_group = categorize_group(std_name, orig_group, group_db)
            if target_group == "DROP": continue
            
            # 将 display_name 传入任务，供探测时盲猜画质使用
            tasks.append({'url': url, 'std_name': std_name, 'orig_name': display_name, 'tvg_logo': tvg_logo, 'group': target_group})

    # 第四步：多线程 HTTP 极速探测
    print("\n")
    valid_count, processed_count = 0, 0
    grouped_channels = {}

    # 使用 30 并发，兼顾速度与防拦截
    with ThreadPoolExecutor(max_workers=30) as executor:
        future_to_task = {executor.submit(probe_url_and_guess_quality, t['url'], t['orig_name']): t for t in tasks}
        
        for future in as_completed(future_to_task):
            t = future_to_task[future]
            processed_count += 1
            height = future.result()
            
            if height == -1: 
                # 探测失败，计入黑名单
                fc = blacklist.get(t['url'], 0)
                blacklist[t['url']] = (fc if isinstance(fc, int) else 0) + 1
            else:
                # 探测成功，清理黑名单并按画质入库
                blacklist[t['url']] = 0 
                
                cat = 'sd' # < 720
                if height >= 2160: cat = '4k'
                elif height >= 720: cat = 'hd'
                
                name_key = t['std_name']
                if name_key not in grouped_channels:
                    grouped_channels[name_key] = {'4k':[], 'hd':[], 'sd':[]}
                
                grouped_channels[name_key][cat].append({'url': t['url'], 'height': height, 'logo': t['tvg_logo'], 'group': t['group']})
                valid_count += 1
            
            print_progress(processed_count, len(tasks), start_time, valid_count)
            
    print("\n")
    save_json(BLACKLIST_JSON, blacklist)

    # 第五步：去重与降级逻辑
    final_playlist = []
    
    for name, cats in grouped_channels.items():
        # 同画质级别内根据“高度”倒序排（因已盲猜画质，此处主要为占位逻辑）
        for c in cats.values(): c.sort(key=lambda x: x['height'], reverse=True)
        
        best_4k = cats['4k'][0] if cats['4k'] else None
        best_sub4k = cats['hd'][0] if cats['hd'] else None
        
        base_group = (best_4k['group'] if best_4k else (cats['hd'][0]['group'] if cats['hd'] else (cats['sd'][0]['group'] if cats['sd'] else "")))
                     
        # 规则2：如果 HD 为空，且是央视/卫视，保留最高的一个 SD 兜底
        if not best_sub4k and base_group in ["央视频道", "地方卫视"] and cats['sd']:
            best_sub4k = cats['sd'][0]
        elif not best_sub4k and not best_4k:
            stats['quality_drop'] += sum(len(x) for x in cats.values())

        # 规则7：4K频道的重分组 (央视/卫视/地方台的4K放入4K频道组)
        if best_4k:
            if base_group in ["央视频道", "地方卫视", "地方频道"] or best_4k['height'] >= 4320:
                best_4k['group'] = "4K频道"
            final_playlist.append({**best_4k, 'name': name})
            
        if best_sub4k:
            final_playlist.append({**best_sub4k, 'name': name})

    # 排序规则5：组序 -> 央视1-17序 -> 名字
    def sort_key(item):
        grp_idx = GROUP_ORDER.index(item['group']) if item['group'] in GROUP_ORDER else 999
        cctv_idx = 999
        if item['name'].startswith('CCTV'):
            m = re.search(r'CCTV-?(\d+)', item['name'])
            if m: cctv_idx = int(m.group(1))
        return (grp_idx, cctv_idx, item['name'])
        
    final_playlist.sort(key=sort_key)

    # 第六步：写入 tv.m3u
    logger.info("生成最终文件 tv.m3u ...")
    with open(OUTPUT_M3U, 'w', encoding='utf-8') as f:
        # 规则13：注入备用 EPG
        f.write('#EXTM3U x-tvg-url="https://epg.112114.xyz/"\n')
        for item in final_playlist:
            raw_name = item['name']
            display_name = CCTV_MAP.get(raw_name, raw_name)
            # 规则12：优先使用原 logo 与分组信息
            extinf = f'#EXTINF:-1 tvg-id="{raw_name}" tvg-name="{raw_name}" tvg-logo="{item["logo"]}" group-title="{item["group"]}", {display_name}'
            f.write(f'{extinf}\n{item["url"]}\n')
            
    # 任务报告
    total_time = time.time() - start_time
    report = f"""
====================================
           📺 IPTV 任务报告
====================================
🔹 初始源梳理数量: {stats['total']}
🔹 黑名单过滤拦截: {stats['bl_drop']}
🔹 关键词过滤丢弃: {stats['kw_drop']}
🔹 低画质淘汰抛弃: {stats['quality_drop']}
✅ 最终保留可用源: {len(final_playlist)}
⏱️ 探测及处理总耗时: {total_time:.2f} 秒
====================================
"""
    print(report)
    logger.info(report.replace('\n', ' '))

if __name__ == "__main__":
    main()