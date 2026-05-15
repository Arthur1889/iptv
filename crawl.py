import os, platform, subprocess, sys, json, re, time, ssl, warnings, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ================= 0. 环境与日志配置 =================
warnings.filterwarnings("ignore")
os.environ["WERKZEUG_RUN_MAIN"] = "true"
ssl._create_default_https_context = ssl._create_unverified_context

# 日志配置：形成 crawl.log 文件
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s', 
    handlers=[
        logging.FileHandler("crawl.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def get_ffmpeg_command():
    """判断系统环境，确定 ffprobe 路径"""
    os_type = platform.system()
    if os_type == "Windows":
        # 白天在 Windows 电脑使用专用路径
        win_path = r"C:\ffmpeg\bin\ffprobe.exe"
        if os.path.exists(win_path):
            return win_path
    # 晚上在 Mac 或 Ubuntu 使用系统自带命令
    return "ffprobe"

def ensure_dependencies():
    for lib in ["requests", "tqdm"]:
        try:
            __import__(lib)
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", lib])

ensure_dependencies()
import requests
from tqdm import tqdm
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 1. 常量与排序规则 =================
CONFIG_FILE = "sources.json"
NAME_JSON = "name.json"
BLACKLIST_FILE = "blacklist.json"

# 第五步：定义的排序分组
GROUP_ORDER = [
    "4K频道", "央视频道", "地方卫视", "港澳台", "山东频道", "数字频道", 
    "影视频道", "纪录纪实", "娱乐频道", "少儿动画", "体育赛事", 
    "歌曲及音乐MV", "外语频道", "综合频道"
]
GROUP_PRIORITY = {name: i for i, name in enumerate(GROUP_ORDER)}

# 央视频道显示名称及描述
CCTV_DESC = {
    "CCTV1": "综合", "CCTV2": "财经", "CCTV3": "综艺", "CCTV4": "中文国际", 
    "CCTV5": "体育", "CCTV5+": "体育赛事", "CCTV6": "电影", "CCTV7": "国防军事", 
    "CCTV8": "电视剧", "CCTV9": "纪录", "CCTV10": "科教", "CCTV11": "戏曲", 
    "CCTV12": "社会与法", "CCTV13": "新闻", "CCTV14": "少儿", "CCTV15": "音乐", 
    "CCTV16": "奥林匹克", "CCTV17": "农业农村"
}

# ================= 2. 核心清洗与匹配逻辑 =================
def clean_display_name(name):
    """要求9：删除分辨率、HD、Not 24/7等字眼"""
    # 移除括弧内容
    name = re.sub(r'[\[\(\（\【\《].*?[\]\)\）\】\应用\》]', '', name)
    # 移除特定敏感字眼
    noise = r'(?i)\b(4K|8K|高清|HD|1080P|720P|蓝光|BD|超清|Geo-blocked|Not 24/7)\b'
    name = re.sub(noise, '', name)
    return name.replace('-', '').replace(' ', '').strip()

def get_standard_info(raw_name, name_map):
    """第二、三步：提取 tvg-id/name 并匹配 name.json"""
    pure = clean_display_name(raw_name)
    
    # 优先识别央视（要求8：排序依据）
    cctv_match = re.search(r'CCTV[-]?(\d+[\+]?)', pure, re.I)
    if cctv_match:
        num = cctv_match.group(1).upper()
        tid = f"CCTV{num}"
        tname = f"CCTV-{num}"
        desc = CCTV_DESC.get(tid, "")
        # 央视显示名称带描述
        return tid, tname, f"{tname} {desc}".strip()
    
    # 第三步：匹配 name.json
    if pure in name_map:
        std_id = name_map[pure]
        return std_id, std_id, std_id
        
    return pure, pure, pure

def get_group_and_final_name(display_name, height, raw_name):
    """要求10：4K分组逻辑及普通分组"""
    is_4k_res = height >= 2160
    is_cctv_or_provincial = any(k in display_name for k in ["CCTV", "卫视"])
    
    # 要求10：4K组只放央视/卫视且分辨率达标的
    if is_4k_res and is_cctv_or_provincial:
        return "4K频道", f"{display_name} 4K"
    
    # 普通分组逻辑
    n = display_name.upper()
    if "CCTV" in n: return "央视频道", display_name
    if "卫视" in n:
        if any(k in n for k in ["凤凰", "TVB", "翡翠", "亚洲", "中视", "华视"]): return "港澳台", display_name
        return "地方卫视", display_name
    if "山东" in n: return "山东频道", display_name
    if any(k in n for k in ["电影", "影院", "CHC", "剧场"]): return "影视频道", display_name
    if any(k in n for k in ["纪录", "纪实", "探索", "求索"]): return "纪录纪实", display_name
    if any(k in n for k in ["体育", "足球", "竞赛", "五星"]): return "体育赛事", display_name
    return "综合频道", display_name

# ================= 3. 主程序 =================
def run():
    start_time = time.time()
    ffprobe_path = get_ffmpeg_command()
    logger.info(f"系统环境检测完毕，使用探测器: {ffprobe_path}")

    # A. 加载 name.json (第三步)
    name_map = {}
    if os.path.exists(NAME_JSON):
        try:
            with open(NAME_JSON, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
                for std_id, aliases in raw_data.items():
                    for alias in aliases.split(','):
                        name_map[clean_display_name(alias)] = std_id
        except: pass

    # B. 第一步：提取源与初步过滤
    if not os.path.exists(CONFIG_FILE): return
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        urls = json.load(f).get("urls", [])

    all_channels, seen_urls = [], set()
    stats = {"raw": 0, "catvod": 0, "junk": 0, "failed": 0, "passed": 0}

    for url in urls:
        try:
            r = requests.get(url, timeout=15, verify=False)
            lines = r.text.split('\n')
            for i, line in enumerate(lines):
                if line.startswith('#EXTINF:'):
                    raw_name = line.split(',')[-1].strip()
                    link = lines[i+1].strip() if i+1 < len(lines) else ""
                    
                    stats["raw"] += 1
                    # 要求6：过滤 catvod
                    if "catvod.com" in link: stats["catvod"] += 1; continue
                    # 要求5：删除直播室
                    if any(k in raw_name for k in ["直播室", "轮播", "专题"]): stats["junk"] += 1; continue
                    
                    if link and link not in seen_urls:
                        all_channels.append({"raw_name": raw_name, "url": link})
                        seen_urls.add(link)
        except: continue

    # C. 第四步：链接探测 (带进度条、速度、优质源统计)
    pool = {} # tid -> { "4K": [best], "HD": [best] }
    with ThreadPoolExecutor(max_workers=30) as executor:
        def check(ch):
            cmd = [ffprobe_path, '-v', 'error', '-show_entries', 'stream=height,bit_rate', '-of', 'json', '-select_streams', 'v:0', ch['url']]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                probe = json.loads(res.stdout)
                if 'streams' in probe and len(probe['streams']) > 0:
                    s = probe['streams'][0]
                    return int(s.get('height', 0)), int(s.get('bit_rate', 0))
            except: pass
            return 0, 0

        futures = {executor.submit(check, ch): ch for ch in all_channels}
        # 要求1：进度条显示进度、速度、优质源
        with tqdm(total=len(all_channels), desc="[探测进度]", unit="it", bar_format='{l_bar}{bar:20}{r_bar}') as pbar:
            for f in as_completed(futures):
                ch = futures[f]
                h, br = f.result()
                if h > 0:
                    stats["passed"] += 1
                    tid, tname, display = get_standard_info(ch['raw_name'], name_map)
                    ch.update({'h': h, 'br': br, 'tid': tid, 'tname': tname, 'display': display, 'score': h*1000 + br/1000})
                    
                    pool.setdefault(tid, {"4K": [], "HD": []})
                    # 要求7：去重逻辑 (4K以上一个，以下一个)
                    if h >= 2160: pool[tid]["4K"].append(ch)
                    else: pool[tid]["HD"].append(ch)
                else:
                    stats["failed"] += 1
                pbar.set_postfix({"优质": stats["passed"], "失败": stats["failed"]})
                pbar.update(1)

    # D. 择优与准入过滤 (要求4：画质筛选)
    final_list = []
    for tid, buckets in pool.items():
        for b_type in ["4K", "HD"]:
            sources = buckets[b_type]
            if not sources: continue
            # 内部去重，保留分数最高的
            sources.sort(key=lambda x: x['score'], reverse=True)
            best = sources[0]
            
            group, final_name = get_group_and_final_name(best['display'], best['h'], best['raw_name'])
            
            # 要求4：准入逻辑 (央卫保底 1 个，其他需 >= 720P)
            is_cctv_provincial = any(k in group for k in ["央视", "卫视"])
            if is_cctv_provincial or best['h'] >= 720:
                final_list.append({**best, "group": group, "final_name": final_name})

    # E. 排序 (要求8：央视1-17)
    def sort_key(x):
        g_priority = GROUP_PRIORITY.get(x['group'], 99)
        # 央视内部 1-17 排序
        cctv_idx = 999
        if "CCTV" in x['tid']:
            m = re.search(r'\d+', x['tid'])
            if m: cctv_idx = int(m.group())
        return (g_priority, cctv_idx, x['final_name'])

    final_list.sort(key=sort_key)

    # F. 第六步：生成 M3U 文件
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            f.write(f'#EXTINF:-1 tvg-id="{ch["tid"]}" tvg-name="{ch["tname"]}" tvg-logo="https://live.fanmingming.com/tv/{ch["tid"]}.png" group-title="{ch["group"]}",{ch["final_name"]}\n{ch["url"]}\n')

    # G. 要求2：生成摘要报告
    duration = int(time.time() - start_time)
    summary = f"""
==================================================
              IPTV 探测任务摘要报告
==================================================
1. 初始源梳理:
   - 抓取总数: {stats['raw']}
   - 屏蔽 CatVod: {stats['catvod']}
   - 屏蔽直播室相关: {stats['junk']}

2. 探测与过滤:
   - 探测失败: {stats['failed']}
   - 优质源产出: {stats['passed']}
   - 最终入选: {len(final_list)} (经 720P 筛选及 4K/HD 去重)

3. 任务情况:
   - 探测器: {ffprobe_path}
   - 系统环境: {platform.system()}
   - 总耗时: {duration // 60}分{duration % 60}秒
==================================================
    """
    logger.info(summary)

if __name__ == "__main__":
    run()