import os
import sys
import re
import json
import time
import logging
import platform
import urllib.request
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

# 判断系统环境 (基本规范)
SYSTEM_OS = platform.system().lower()
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

SOURCES_JSON_PATH = os.path.join(CURRENT_DIR, "sources.json")
NAME_JSON_PATH = os.path.join(CURRENT_DIR, "name.json")
BLACKLIST_JSON_PATH = os.path.join(CURRENT_DIR, "blacklist.json")
OUTPUT_M3U_PATH = os.path.join(CURRENT_DIR, "tv.m3u")
LOG_FILE_PATH = os.path.join(CURRENT_DIR, "crawl.log")

# 1. 配置日志规范并形成文件 (要求1)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE_PATH, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

print_lock = Lock()
last_print_time = 0

# 央视频道标准中文描述映射 (要求6)
CCTV_DESC_MAP = {
    "CCTV1": "CCTV-1 综合", "CCTV2": "CCTV-2 财经", "CCTV3": "CCTV-3 综艺",
    "CCTV4": "CCTV-4 中文国际", "CCTV5": "CCTV-5 体育", "CCTV5+": "CCTV-5+ 体育赛事",
    "CCTV6": "CCTV-6 电影", "CCTV7": "CCTV-7 国防军事", "CCTV8": "CCTV-8 电视剧",
    "CCTV9": "CCTV-9 纪录", "CCTV10": "CCTV-10 科教", "CCTV11": "CCTV-11 戏曲",
    "CCTV12": "CCTV-12 社会与法", "CCTV13": "CCTV-13 新闻", "CCTV14": "CCTV-14 少儿",
    "CCTV15": "CCTV-15 音乐", "CCTV16": "CCTV-16 奥林匹克", "CCTV17": "CCTV-17 农业农村"
}

# 排序优先级权重字典 (第五步、要求5)
GROUP_PRIORITY = [
    "4K频道", "央视频道", "地方卫视", "港澳台", "山东频道", "上海频道", 
    "地方频道", "影视频道", "歌曲及音乐MV", "纪录纪实", "娱乐频道", 
    "少儿动画", "体育赛事", "海外频道", "综合频道", "电视剧直播", "动漫直播"
]

PROVINCES = [
    "北京", "上海", "天津", "重庆", "广东", "山东", "浙江", "江苏", "安徽", "福建", 
    "江西", "湖北", "河南", "河北", "山西", "吉林", "辽宁", "广西", "四川", "贵州", 
    "云南", "陕西", "甘肃", "青海", "宁夏", "新疆", "海南", "西藏", "黑龙江", "内蒙古"
]

# 🌟 补齐缺失的核心函数：从 sources.json 的源列表中提取源
def get_target_urls():
    if not os.path.exists(SOURCES_JSON_PATH):
        # 兜底兼容旧格式文件
        old_path = os.path.join(CURRENT_DIR, "sources.txt")
        if os.path.exists(old_path):
            with open(old_path, 'r', encoding='utf-8') as f:
                return [line.strip() for line in f if line.strip().startswith("http")]
        return []
    try:
        with open(SOURCES_JSON_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # 兼容字典格式或数组格式的 sources.json
            if isinstance(data, list):
                return [u.strip() for u in data if str(u).strip().startswith("http")]
            elif isinstance(data, dict):
                urls = []
                for v in data.values():
                    if isinstance(v, list): urls.extend(v)
                    elif isinstance(v, str): urls.append(v)
                return [u.strip() for u in urls if str(u).strip().startswith("http")]
    except Exception as e:
        logging.error(f"解析 sources.json 失败: {e}")
    return []

def load_json_file(path):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f: return json.load(f)
        except Exception as e:
            logging.error(f"加载 {os.path.basename(path)} 失败: {e}")
    return {}

def save_json_file(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"存储 {os.path.basename(path)} 失败: {e}")

def clean_display_name(name):
    if not name: return ""
    # 清理要求6指定的各种乱糟糟的画质和阻断后缀
    junk_words = [
        r'2160p', r'1080p', r'720p', r'480p', r'404p', r'360p', 
        r'hd', r'sd', r'\[Not 24/7\]', r'\[Geo-blocked\]', r'超高清', r'高清'
    ]
    cleaned = name
    for pattern in junk_words:
        cleaned = re.sub(pattern, '', cleaned, flags=re.I)
    cleaned = re.sub(r'\(\s*\)|\[\s*\]|（\s*）|【\s*】', '', cleaned)
    return cleaned.strip(" -_|+= ")

def normalize_cctv_name(name):
    upper_name = name.upper().replace("-", "").replace(" ", "")
    if "CCTV5+" in upper_name or "CCTV5PLUS" in upper_name: return "CCTV5+"
    if "CCTV4K" in upper_name: return "CCTV4K" 
    if "CCTV8K" in upper_name: return "CCTV8K" 
    match = re.search(r'CCTV(\d+)', upper_name)
    if match: return f"CCTV{match.group(1)}"
    return name

def determine_final_group(std_name, raw_group, is_4k_8k):
    """
    结合13条分组规则和特定Emoji组别进行归类 (要求7、分组规则)
    """
    name_up = std_name.upper()
    rg = raw_group.strip() if raw_group else ""

    # 1. 强力过滤名单：规则11指定的直接死刑组，返回 None 代表丢弃
    if any(x in rg for x in ["游戏直播", "听书直播", "老年直播", "解说直播", "监控直播", "蜘蛛直播", "zuqiu直播", "咪视界直播", "KK直播", "瑜伽裤直播", "Ai直播", "钓鱼直播", "API随机点播"]):
        return None

    # 2. 4K/8K 核心规范组拦截 (要求7)
    is_cctv = "CCTV" in name_up or "中央台" in name_up
    is_ws = "卫视" in name_up
    is_df = any(x in name_up for x in PROVINCES) or "频道" in name_up
    if is_4k_8k and (is_cctv or is_ws or is_df):
        return "4K频道"

    # 3. 按照规则映射原始组别名
    if "地方台直播" in rg: return "地方频道"
    if "港澳台直播" in rg: return "港澳台"
    if any(x in rg for x in ["延伸西亚", "马来西亚直播", "越南直播", "印度直播", "日本直播", "韩国直播", "美国直播", "英国直播", "爱尔兰直播", "全球直播"]): return "海外频道"
    if "少儿直播" in rg: return "少儿动画"
    if "体育直播" in rg: return "体育赛事"
    if "电影直播" in rg: return "影视频道"
    if any(x in rg for x in ["综艺直播", "短剧直播", "小品直播", "相声直播", "抖音直播", "YY直播", "车模直播", "女团直播", "热舞直播", "乡野直播", "脱口秀直播"]): return "娱乐频道"
    if any(x in rg for x in ["电视剧直播", "爱奇艺直播", "埋堆堆直播"]): return "电视剧直播"
    if "纪录片直播" in rg: return "纪录纪实"
    if any(x in rg for x in ["动漫直播", "沙雕动画直播"]): return "动漫直播"
    if any(x in rg for x in ["音乐直播", "周杰伦歌曲", "歌手合集"]): return "歌曲及音乐MV"

    # 4. 保底兜底逻辑分类
    if is_cctv: return "央视频道"
    if is_ws: return "地方卫视"
    if "上海" in std_name: return "上海频道"
    if "山东" in std_name or "齐鲁" in std_name: return "山东频道"
    for prov in PROVINCES:
        if prov in std_name: return "地方频道"

    if any(x in name_up for x in ["港", "澳", "台", "HBO", "PHOENIX", "凤凰"]): return "港澳台"
    if any(x in name_up for x in ["电影", "影院", "剧场", "影视"]): return "影视频道"
    if any(x in name_up for x in ["纪录", "纪实", "探索"]): return "纪录纪实"
    if any(x in name_up for x in ["动漫", "少儿", "卡通", "儿童"]): return "少儿动画"
    if any(x in name_up for x in ["体育", "赛事", "足球"]): return "体育赛事"
    if any(x in name_up for x in ["音乐", "MV", "歌曲"]): return "歌曲及音乐MV"
    
    return "综合频道"

def parse_m3u_content(text, name_repo, blacklist, stats):
    parsed_list = []
    lines = text.splitlines()
    current_meta = None

    for line in lines:
        line = line.strip()
        if not line: continue
        if line.startswith("#EXTINF:"):
            tid = re.search(r'tvg-id="([^"]*)"', line).group(1) if 'tvg-id="' in line else ""
            tname = re.search(r'tvg-name="([^"]*)"', line).group(1) if 'tvg-name="' in line else ""
            tlogo = re.search(r'tvg-logo="([^"]*)"', line).group(1) if 'tvg-logo="' in line else ""
            tgroup = re.search(r'group-title="([^"]*)"', line).group(1) if 'group-title="' in line else ""
            dname = line.split(",")[-1].strip()
            current_meta = {
                "raw_id": tid, "raw_name": tname, "logo": tlogo, "raw_group": tgroup, "display_name": dname
            }
        elif line.startswith("http") and current_meta:
            url = line
            
            if "catvod.com" in url or url in blacklist:
                stats["filtered_blacklist"] += 1
                current_meta = None
                continue

            raw_upper = (tid + tname + dname).upper()
            has_4k_label = "4K" in raw_upper or "2160P" in raw_upper
            has_8k_label = "8K" in raw_upper or "4320P" in raw_upper

            dname_clean = clean_display_name(current_meta["display_name"])
            matched_std = None
            for key, val in name_repo.items():
                aliases = [a.strip() for a in val.split(",")]
                if dname_clean in aliases or current_meta["display_name"] in aliases or dname_clean == key:
                    matched_std = key
                    break
            
            final_std_name = matched_std if matched_std else dname_clean
            if "CCTV" in final_std_name.upper():
                final_std_name = normalize_cctv_name(final_std_name)

            current_meta.update({
                "url": url, "std_name": final_std_name, 
                "has_4k_label": has_4k_label, "has_8k_label": has_8k_label
            })
            parsed_list.append(current_meta)
            current_meta = None

    return parsed_list

def probe_stream(item, timeout=4):
    start_time = time.time()
    try:
        req = urllib.request.Request(item["url"], headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            delay = time.time() - start_time
            chunk = response.read(100000)
            chunk_str = chunk.decode('utf-8', errors='ignore') if chunk else ""
            
            height = 1080 
            resolution_match = re.search(r'RESOLUTION=\d+x(\d+)', chunk_str)
            if resolution_match:
                height = int(resolution_match.group(1))
            else:
                if item["has_8k_label"]: height = 4320
                elif item["has_4k_label"]: height = 2160
                elif "m3u8" not in item["url"].lower() and len(chunk) < 20000: height = 480

            is_4k_8k = height >= 2160 or item["has_4k_label"] or item["has_8k_label"]
            return {"valid": True, "delay": delay, "height": height, "is_4k_8k": is_4k_8k, "item": item}
    except Exception:
        return {"valid": False, "item": item}

def render_progress_bar(completed, total, quality_count, start_time):
    global last_print_time
    now = time.time()
    if now - last_print_time < 0.1 and completed < total: return
    last_print_time = now
    if total == 0: return

    pct = (completed / total) * 100
    bar_len = 20
    filled = int(bar_len * completed // total)
    bar = '█' * filled + '-' * (bar_len - filled)
    elapsed = now - start_time
    speed = completed / elapsed if elapsed > 0 else 0
    
    sys.stdout.write(f"\r🔍 探测进度: [{bar}] {pct:.1f}% | 速度: {speed:.1f}条/s | 已检: {completed}/{total} | 优质源(>=4K): {quality_count}个\033[K")
    sys.stdout.flush()

def get_cctv_sort_key(name):
    if "CCTV5+" in name.upper(): return 5.5
    if "CCTV4K" in name.upper(): return 18
    if "CCTV8K" in name.upper(): return 19
    num = re.search(r'CCTV(\d+)', name, re.I)
    return int(num.group(1)) if num else 999

def main():
    start_run_time = time.time()
    logging.info("==============================================")
    logging.info(f" 🚀 IPTV 管道工自动化分拣系统启动 | 环境: {SYSTEM_OS.upper()}")
    logging.info("==============================================")

    stats = {"total_raw": 0, "filtered_blacklist": 0, "low_res_filtered": 0, "final_count": 0}
    
    fail_counter = defaultdict(int)
    blacklist_data = load_json_file(BLACKLIST_JSON_PATH)
    name_repo = load_json_file(NAME_JSON_PATH)
    
    target_urls = get_target_urls()
    if not target_urls:
        logging.error("未在 sources.json 中扫到有效配置源，终止清洗。")
        return

    all_raw_channels = []
    for url in set(target_urls):
        try:
            logging.info(f"-> 正在跨境拉取数据源: {url}")
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                text = r.read().decode('utf-8', errors='ignore')
                all_raw_channels.extend(parse_m3u_content(text, name_repo, blacklist_data, stats))
        except Exception as e:
            logging.warning(f"   ⚠️ 数据源加载受阻跳过: {e}")

    stats["total_raw"] = len(all_raw_channels) + stats["filtered_blacklist"]
    logging.info(f"-> 【粗筛大池固化】物理全量明细共计: {len(all_raw_channels)} 条")

    pre_bucket = defaultdict(list)
    for item in all_raw_channels:
        if len(pre_bucket[item["std_name"]]) < 8:
            pre_bucket[item["std_name"]].append(item)
    
    test_queue = []
    for items in pre_bucket.values(): test_queue.extend(items)

    logging.info(f"-> 启动高并发网络探测，测速队列精简后剩余: {len(test_queue)} 条")
    
    probed_results = []
    completed_tasks = 0
    quality_count = 0
    probe_start_time = time.time()

    with ThreadPoolExecutor(max_workers=100) as executor:
        futures = {executor.submit(probe_stream, item): item for item in test_queue}
        for future in as_completed(futures):
            res = future.result()
            completed_tasks += 1
            if res["valid"]:
                probed_results.append(res)
                if res["is_4k_8k"]: quality_count += 1
            else:
                bad_url = res["item"]["url"]
                fail_counter[bad_url] += 1
                if fail_counter[bad_url] >= 3:
                    blacklist_data[bad_url] = "连挂三次黑名单阻断"
            render_progress_bar(completed_tasks, len(futures), quality_count, probe_start_time)
    print("\n")

    dedup_bucket = defaultdict(lambda: {"4k_best": None, "normal_best": None})
    for res in probed_results:
        std_name = res["item"]["std_name"]
        if res["is_4k_8k"]:
            cur = dedup_bucket[std_name]["4k_best"]
            if not cur or res["delay"] < cur["delay"]: dedup_bucket[std_name]["4k_best"] = res
        else:
            cur = dedup_bucket[std_name]["normal_best"]
            if not cur or res["delay"] < cur["delay"]: dedup_bucket[std_name]["normal_best"] = res

    final_verified_list = []
    for std_name, bucket in dedup_bucket.items():
        is_cctv_or_ws = "CCTV" in std_name.upper() or "卫视" in std_name
        has_output = False
        
        if bucket["4k_best"]:
            final_verified_list.append(bucket["4k_best"])
            has_output = True
        
        if bucket["normal_best"]:
            if bucket["normal_best"]["height"] < 720:
                if is_cctv_or_ws and not has_output:
                    final_verified_list.append(bucket["normal_best"])
                else:
                    stats["low_res_filtered"] += 1
            else:
                final_verified_list.append(bucket["normal_best"])

    def master_sort_key(res_obj):
        item = res_obj["item"]
        std_name = item["std_name"]
        group = determine_final_group(std_name, item["raw_group"], res_obj["is_4k_8k"])
        
        if not group: return (9999, 9999, 9999)
        
        group_idx = GROUP_PRIORITY.index(group) if group in GROUP_PRIORITY else 999
        cctv_idx = get_cctv_sort_key(std_name) if group in ["央视频道", "4K频道"] else 999
        return (group_idx, cctv_idx, res_obj["delay"])

    final_verified_list.sort(key=master_sort_key)

    written_count = 0
    with open(OUTPUT_M3U_PATH, "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.cn/e.xml,http://epg.51zmt.top:12210/e.xml" catchup="append" catchup-source="?playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}"\n')
        
        for res in final_verified_list:
            item = res["item"]
            std_name = item["std_name"]
            final_group = determine_final_group(std_name, item["raw_group"], res["is_4k_8k"])
            if not final_group: continue 

            display_title = std_name
            upper_std = std_name.upper().replace("-", "").replace(" ", "")
            
            if "CCTV" in upper_std and upper_std in CCTV_DESC_MAP:
                display_title = CCTV_DESC_MAP[upper_std]

            formatted_tvg = display_title.split(" ")[0].replace("-", "") if "CCTV" in display_title else std_name
            
            f.write(f'#EXTINF:-1 tvg-id="{formatted_tvg}" tvg-name="{formatted_tvg}" tvg-logo="{item["logo"]}" group-title="{final_group}",{display_title}\n')
            f.write(f'{item["url"]}\n')
            written_count += 1

    stats["final_count"] = written_count
    save_json_file(BLACKLIST_JSON_PATH, blacklist_data)

    duration = time.time() - start_run_time
    logging.info("==============================================")
    logging.info("        Crawl 管道自动化清洗任务总结报告")
    logging.info("==============================================")
    logging.info(f"- 初始总计扫入原始数据   : {stats['total_raw']} 条")
    logging.info(f"- 命中黑名单/猫片阻断数  : {stats['filtered_blacklist']} 条")
    logging.info(f"- 垃圾低分辨率拦截过滤   : {stats['low_res_filtered']} 条")
    logging.info(f"- 最终输出 tv.m3u 有效源 : {stats['final_count']} 条")
    logging.info(f"- 任务运行总计消耗时长   : {duration:.2f} 秒")
    logging.info("==============================================")

if __name__ == "__main__":
    main()