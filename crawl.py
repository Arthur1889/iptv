import os, platform, subprocess, sys, json, re, time, ssl, warnings, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

# ================= 0. 基础配置与日志 =================
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

# ================= 1. 常量与映射 =================
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

# ================= 2. 核心工具函数 =================

def clean_display_name(name):
    """ 10. 清除名称杂质，防止出现'44K'等异常 """
    # 先移除括号、书名号内的内容
    name = re.sub(r'[\[\(\（\【\《].*?[\]\)\）\】\》]', '', name)
    # 移除特定后缀（使用单词边界 \b 防止误杀频道名中的数字）
    noise = r'(?i)\b(4K|8K|高清|超高清|蓝光|BD|1080P|720P|HD|Not 24/7|Geo-blocked)\b'
    name = re.sub(noise, '', name)
    # 移除连字符和多余空格
    return name.replace('-', '').strip()

def get_standard_info(raw_name, name_map):
    """ 第三步：匹配标准化名称，支持 CCTV 自动补全中文描述 """
    s_name = clean_display_name(raw_name)
    # CCTV 提取与描述补全
    cctv_match = re.search(r'CCTV[-]?(\d+[\+]?)', s_name, re.I)
    if cctv_match:
        num = cctv_match.group(1).upper()
        tid = f"CCTV{num}"
        tname = f"CCTV-{num}"
        desc = CCTV_DESC.get(tid, "")
        display = f"{tname} {desc}".strip()
        return tid, tname, display

    # 匹配 name.json
    if s_name in name_map:
        target = name_map[s_name]
        return target, target, target
        
    return s_name, s_name, s_name

def get_group(display_name, height):
    """ 7 & 11. 分类排序与 4K 隔离逻辑 """
    n = display_name.upper()
    is_cv = any(k in n for k in ["CCTV", "卫视"])
    
    # 11. 4K频道逻辑
    if height >= 2160 and is_cv: return "4K频道"
    
    if "CCTV" in n: return "央视频道"
    if "卫视" in n:
        if any(k in n for k in ["凤凰", "TVB", "翡翠", "亚洲", "中视", "华视", "星空"]): return "港澳台"
        return "地方卫视"
    if "山东" in n: return "山东频道"
    if any(k in n for k in ["电影", "影院", "CHC", "剧场", "私人"]): return "影视频道"
    if any(k in n for k in ["纪录", "纪实", "探索", "求索", "地理"]): return "纪录纪实"
    if any(k in n for k in ["歌曲", "音乐", "MV", "DJ"]): return "歌曲及音乐MV"
    if any(k in n for k in ["少儿", "卡通", "动漫", "动画"]): return "少儿动画"
    if any(k in n for k in ["体育", "足球", "竞赛", "竞技", "五星"]): return "体育赛事"
    if any(k in n for k in ["CNN", "BBC", "HBO", "FOX", "NHK"]): return "外语频道"
    return "综合频道"

def analyze_stream(url, timeout):
    """ 第四步：探测 """
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'stream=width,height,bit_rate', '-of', 'json', '-select_streams', 'v:0', url]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors='ignore')
        s = json.loads(res.stdout)['streams'][0]
        return int(s.get('height', 0)), int(s.get('bit_rate', 0))
    except: return 0, 0

# ================= 3. 主程序 =================

def run():
    start_time = time.time()
    workers, timeout = 40, 20
    
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

    stats = {"raw": 0, "catvod_drop": 0, "junk_drop": 0, "bl_skip": 0, "ffmpeg_drop": 0, "passed": 0}

    # 第一步：提取源
    if not os.path.exists(CONFIG_FILE): return
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f: urls = json.load(f).get("urls", [])

    all_channels, seen_urls = [], set()
    for url in urls:
        try:
            r = requests.get(url, timeout=10, verify=False)
            lines = r.text.split('\n')
            for i in range(len(lines)):
                if lines[i].startswith('#EXTINF:'):
                    raw_name = lines[i].split(',')[-1].strip()
                    link = lines[i+1].strip() if i+1 < len(lines) else ""
                    stats["raw"] += 1
                    
                    if "catvod.com" in link: stats["catvod_drop"] += 1; continue
                    if any(k in raw_name for k in ["直播室", "轮播", "专题", "课堂"]): stats["junk_drop"] += 1; continue
                    if link in bl_data and bl_data[link].get('fail_count', 0) >= 3:
                        stats["bl_skip"] += 1; continue
                    
                    if link and link not in seen_urls:
                        all_channels.append({"raw_name": raw_name, "url": link})
                        seen_urls.add(link)
        except: continue

    # 第四步：探测与分桶
    pool = {} 
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(analyze_stream, ch['url'], timeout): ch for ch in all_channels}
        # 1. 进度条：含有进度条、时间、速度、优质源
        with tqdm(total=len(all_channels), desc="[探测进度]", unit="ch", bar_format='{l_bar}{bar:20}{r_bar} 优质:{postfix}') as pbar:
            for f in as_completed(futures):
                ch = futures[f]
                h, br = f.result()
                if h > 0:
                    stats["passed"] += 1
                    tid, tname, display = get_standard_info(ch['raw_name'], name_map)
                    ch.update({'h': h, 'br': br, 'tid': tid, 'tname': tname, 'display': display, 'score': h*1000 + br/1000})
                    
                    # 8. 双桶去重：sid下设High和Std
                    pool.setdefault(tid, {"High": [], "Std": []})
                    if h >= 2160: pool[tid]["High"].append(ch)
                    else: pool[tid]["Std"].append(ch)
                else:
                    stats["ffmpeg_drop"] += 1
                    info = bl_data.get(ch['url'], {"fail_count": 0})
                    info.update({"fail_count": info["fail_count"] + 1, "last_fail": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                    bl_data[ch['url']] = info
                pbar.set_postfix_str(str(stats["passed"]))
                pbar.update(1)

    # 择优与分发
    final_list = []
    for sid, buckets in pool.items():
        for b_type in ["High", "Std"]:
            sources = buckets[b_type]
            if not sources: continue
            sources.sort(key=lambda x: x['score'], reverse=True)
            best = sources[0]
            
            group = get_group(best['display'], best['h'])
            # 11. 4K频道精准追加
            name_final = best['display']
            if group == "4K频道":
                tag = "4K" if best['h'] < 4320 else "8K"
                name_final = f"{best['display']}{tag}"

            # 4. 准入规则
            if any(k in group for k in ["央视", "卫视"]) or best['h'] >= 720:
                final_list.append({
                    "tid": best['tid'], "tname": best['tname'], "group": group,
                    "display": name_final, "url": best['url']
                })

    # 9. 央视按照 1-17 排序
    def sort_logic(x):
        g_idx = GROUP_PRIORITY.get(x['group'], 99)
        cctv_num = 999
        if "CCTV" in x['tid']:
            m = re.search(r'CCTV(\d+)', x['tid'])
            cctv_num = int(m.group(1)) if m else 0
        return (g_idx, cctv_num, x['display'])

    final_list.sort(key=sort_logic)

    # 第六步：生成 M3U（标准协议标签）
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            f.write(f'#EXTINF:-1 tvg-id="{ch["tid"]}" tvg-name="{ch["tname"]}" tvg-logo="https://live.fanmingming.com/tv/{ch["tid"]}.png" group-title="{ch["group"]}",{ch["display"]}\n{ch["url"]}\n')

    # 更新黑名单文件
    with open(BLACKLIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(bl_data, f, indent=2, ensure_ascii=False)

    # 2. 任务摘要报告（格式对齐左侧）
    duration = int(time.time() - start_time)
    logger.info(f"""
==================================================
              IPTV 探测任务摘要报告
==================================================
1. 初始源梳理:
   - 抓取总数: {stats['raw']} | 屏蔽 CatVod: {stats['catvod_drop']}
   - 屏蔽直播室: {stats['junk_drop']} | 黑名单跳过: {stats['bl_skip']}

2. 探测与去重:
   - 探测失败: {stats['ffmpeg_drop']}
   - 重复处理: 频道级双桶去重 (4K/标清各一)
   - 名称清洗: 已剔除干扰后缀 (HD/Geo-blocked等)

3. 最终情况:
   - 入选频道数: {len(final_list)} 个
   - 排序规则: {len(GROUP_ORDER)} 类分组顺序 + 央视 1-17 精准排序
   - 总耗时: {duration // 60}分{duration % 60}秒
==================================================
""")

if __name__ == "__main__":
    run()