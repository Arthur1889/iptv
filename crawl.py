import os, platform, subprocess, sys, json, re, time, ssl, warnings, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

# ================= 0. 环境与日志配置 =================
warnings.filterwarnings("ignore")
os.environ["WERKZEUG_RUN_MAIN"] = "true" 
ssl._create_default_https_context = ssl._create_unverified_context

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("crawl.log", encoding="utf-8"), logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def ensure_dependencies():
    for lib in ["requests", "tqdm"]:
        try: __import__(lib)
        except ImportError: subprocess.check_call([sys.executable, "-m", "pip", "install", lib])

ensure_dependencies()
import requests
from tqdm import tqdm
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 1. 常量与排序逻辑 =================
CONFIG_FILE = "sources.json"
NAME_JSON = "name.json"
BLACKLIST_FILE = "blacklist.json"

GROUP_ORDER = [
    "4K频道", "央视频道", "地方卫视", "港澳台", "山东频道", "数字频道", 
    "影视频道", "纪录纪实", "娱乐频道", "少儿动画", "体育赛事", "歌曲及音乐MV", "外语频道", "综合频道"
]
GROUP_PRIORITY = {name: i for i, name in enumerate(GROUP_ORDER)}

CCTV_DESC = {
    "CCTV1": "综合", "CCTV2": "财经", "CCTV3": "综艺", "CCTV4": "中文国际",
    "CCTV5": "体育", "CCTV5+": "体育赛事", "CCTV6": "电影", "CCTV7": "国防军事",
    "CCTV8": "电视剧", "CCTV9": "纪录", "CCTV10": "科教", "CCTV11": "戏曲",
    "CCTV12": "社会与法", "CCTV13": "新闻", "CCTV14": "少儿", "CCTV15": "音乐",
    "CCTV16": "奥林匹克", "CCTV17": "农业农村"
}

# ================= 2. 核心逻辑函数 =================

def clean_name_pure(name):
    """ 彻底剥离所有质量、环境标签，用于匹配 id """
    name = re.sub(r'[\[\(\（\【\《].*?[\]\)\）\】\》]', '', name)
    noise = r'(?i)\b(4K|8K|高清|HD|1080P|720P|蓝光|BD|超清|Geo-blocked|Not 24/7)\b'
    name = re.sub(noise, '', name)
    return name.replace('-', '').strip()

def get_standard_info(raw_name, name_map):
    """ 获取 tvg-id, tvg-name 和 基础显示名称 """
    pure = clean_name_pure(raw_name)
    
    # 央视识别
    cctv_match = re.search(r'CCTV[-]?(\d+[\+]?)', pure, re.I)
    if cctv_match:
        num = cctv_match.group(1).upper()
        tid = f"CCTV{num}"
        tname = f"CCTV-{num}"
        desc = CCTV_DESC.get(tid, "")
        return tid, tname, f"{tname} {desc}".strip()

    # 匹配 name.json
    if pure in name_map:
        std = name_map[pure]
        return std, std, std
        
    return pure, pure, pure

def get_group(name_for_group, height):
    """ 判定频道所属分组 """
    n = name_for_group.upper()
    if "CCTV" in n: return "央视频道"
    if "卫视" in n:
        if any(k in n for k in ["凤凰", "TVB", "翡翠", "亚洲", "中视", "华视"]): return "港澳台"
        return "地方卫视"
    if "山东" in n: return "山东频道"
    if any(k in n for k in ["电影", "影院", "CHC", "剧场"]): return "影视频道"
    if any(k in n for k in ["纪录", "纪实", "探索", "求索"]): return "纪录纪实"
    if any(k in n for k in ["体育", "足球", "竞赛", "五星"]): return "体育赛事"
    if any(k in n for k in ["CNN", "BBC", "HBO", "NHK"]): return "外语频道"
    return "综合频道"

# ================= 3. 主执行程序 =================

def run():
    start_time = time.time()
    
    # 加载辅助文件
    name_map = {}
    if os.path.exists(NAME_JSON):
        try:
            with open(NAME_JSON, 'r', encoding='utf-8') as f: name_map = json.load(f)
        except: pass

    bl_data = {}
    if os.path.exists(BLACKLIST_FILE):
        try:
            with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f: bl_data = json.load(f)
        except: pass

    stats = {"raw": 0, "catvod": 0, "junk": 0, "bl_skip": 0, "failed": 0, "4k_match": 0, "passed": 0}

    # 提取源
    if not os.path.exists(CONFIG_FILE): return
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f: urls = json.load(f).get("urls", [])

    all_channels, seen_urls = [], set()
    for url in urls:
        try:
            r = requests.get(url, timeout=10, verify=False)
            for i, line in enumerate(lines := r.text.split('\n')):
                if line.startswith('#EXTINF:'):
                    raw_name = line.split(',')[-1].strip()
                    link = lines[i+1].strip() if i+1 < len(lines) else ""
                    stats["raw"] += 1
                    if "catvod.com" in link: stats["catvod"] += 1; continue
                    if any(k in raw_name for k in ["直播室", "轮播", "专题"]): stats["junk"] += 1; continue
                    if link in bl_data and bl_data[link].get('fail_count', 0) >= 3: stats["bl_skip"] += 1; continue
                    if link and link not in seen_urls:
                        all_channels.append({"raw_name": raw_name, "url": link})
                        seen_urls.add(link)
        except: continue

    # 探测与分桶逻辑
    pool = {} # sid -> {High: [], Std: []}
    with ThreadPoolExecutor(max_workers=40) as executor:
        def check(ch):
            cmd = ['ffprobe', '-v', 'error', '-show_entries', 'stream=height,bit_rate', '-of', 'json', '-select_streams', 'v:0', ch['url']]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                s = json.loads(res.stdout)['streams'][0]
                return int(s.get('height', 0)), int(s.get('bit_rate', 0))
            except: return 0, 0

        futures = {executor.submit(check, ch): ch for ch in all_channels}
        with tqdm(total=len(all_channels), desc="[探测进度]", bar_format='{l_bar}{bar:20}{r_bar} 优质:{postfix}') as pbar:
            for f in as_completed(futures):
                ch = futures[f]
                h, br = f.result()
                if h > 0:
                    stats["passed"] += 1
                    tid, tname, display = get_standard_info(ch['raw_name'], name_map)
                    ch.update({'h': h, 'br': br, 'tid': tid, 'tname': tname, 'display': display, 'score': h*1000 + br/1000})
                    
                    pool.setdefault(tid, {"High": [], "Std": []})
                    if h >= 2160: pool[tid]["High"].append(ch)
                    else: pool[tid]["Std"].append(ch)
                else: stats["failed"] += 1
                pbar.set_postfix_str(str(stats["passed"]))
                pbar.update(1)

    # 择优与“带标且达标”判定
    final_list = []
    for sid, buckets in pool.items():
        for b_type in ["High", "Std"]:
            sources = buckets[b_type]
            if not sources: continue
            sources.sort(key=lambda x: x['score'], reverse=True)
            best = sources[0]
            
            # 关键判定：原始名称是否带4K/8K标签
            has_raw_4k = bool(re.search(r'(4K|8K)', best['raw_name'], re.I))
            
            # 判定分组
            if has_raw_4k and best['h'] >= 2160:
                # 只有带标且达标，才进入4K组，并显式保留标签
                group = "4K频道"
                final_name = f"{best['display']}4K"
                stats["4k_match"] += 1
            else:
                # 否则根据标准名称判定组别
                group = get_group(best['display'], best['h'])
                final_name = best['display']

            # 准入：720P 或 央卫保底
            if any(k in group for k in ["央视", "卫视"]) or best['h'] >= 720:
                final_list.append({**best, "group": group, "final_name": final_name})

    # 排序
    final_list.sort(key=lambda x: (GROUP_PRIORITY.get(x['group'], 99), 
                                  int(re.search(r'\d+', x['tid']).group()) if "CCTV" in x['tid'] and re.search(r'\d+', x['tid']) else 999, 
                                  x['final_name']))

    # 生成 M3U
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            f.write(f'#EXTINF:-1 tvg-id="{ch["tid"]}" tvg-name="{ch["tname"]}" tvg-logo="https://live.fanmingming.com/tv/{ch["tid"]}.png" group-title="{ch["group"]}",{ch["final_name"]}\n{ch["url"]}\n')

    # 摘要报告
    duration = int(time.time() - start_time)
    logger.info(f"""
==================================================
              IPTV 探测任务摘要报告
==================================================
1. 初始源梳理:
   - 抓取总数: {stats['raw']} | 屏蔽 CatVod: {stats['catvod']}
   - 屏蔽直播室: {stats['junk']} | 黑名单跳过: {stats['bl_skip']}

2. 探测与去重:
   - 探测失败: {stats['failed']}
   - 4K 认定: {stats['4k_match']} 个 (原始带标且达标)
   - 重复处理: 频道级双桶去重 (4K与高清各留一)

3. 最终情况:
   - 入选频道数: {len(final_list)} 个
   - 排序规则: 14 类分组顺序 + 央视标准 1-17 排序
   - 总耗时: {duration // 60}分{duration % 60}秒
==================================================
""")

if __name__ == "__main__":
    run()