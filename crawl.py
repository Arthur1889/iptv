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

SYSTEM_OS = platform.system().lower()
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

SOURCES_JSON_PATH = os.path.join(CURRENT_DIR, "sources.json")
NAME_JSON_PATH = os.path.join(CURRENT_DIR, "name.json")
OUTPUT_M3U_PATH = os.path.join(CURRENT_DIR, "tv.m3u")
LOG_FILE_PATH = os.path.join(CURRENT_DIR, "crawl.log")

# 配置日志规范 (要求1：形成log文件)
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

# 央视频道标准中文描述映射 (要求5)
CCTV_DESC_MAP = {
    "CCTV1": "CCTV-1 综合", "CCTV2": "CCTV-2 财经", "CCTV3": "CCTV-3 综艺", 
    "CCTV4": "CCTV-4 中文国际", "CCTV5": "CCTV-5 体育", "CCTV5+": "CCTV-5+ 体育赛事", 
    "CCTV6": "CCTV-6 电影", "CCTV7": "CCTV-7 国防军事", "CCTV8": "CCTV-8 电视剧", 
    "CCTV9": "CCTV-9 纪录", "CCTV10": "CCTV-10 科教", "CCTV11": "CCTV-11 戏曲", 
    "CCTV12": "CCTV-12 社会与法", "CCTV13": "CCTV-13 新闻", "CCTV14": "CCTV-14 少儿", 
    "CCTV15": "CCTV-15 音乐", "CCTV16": "CCTV-16 奥林匹克", "CCTV17": "CCTV-17 农业农村"
}

# 五步排序优先级划分 (要求5、7)
GROUP_PRIORITY = [
    "4K频道", "央视频道", "地方卫视", "港澳台", "山东频道", "数字频道", 
    "影视频道", "纪录纪实", "娱乐频道", "少儿动画", "体育赛事", 
    "歌曲及音乐MV", "外语频道", "综合频道"
]

def load_name_json():
    if os.path.exists(NAME_JSON_PATH):
        try:
            with open(NAME_JSON_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"加载 name.json 失败: {e}")
            return {}
    return {}

def clean_display_name(name):
    if not name: return ""
    # 清理名称后缀字眼 (要求6)
    junk_words = [r'2160p', r'1080p', r'720p', r'576p', r'576i', r'hd', r'sd', r'\[Not 24/7\]', r'\[Geo-blocked\]', r'超高清']
    cleaned = name
    for pattern in junk_words:
        cleaned = re.sub(pattern, '', cleaned, flags=re.I)
    cleaned = re.sub(r'\(\s*\)|\[\s*\]|（\s*）|【\s*】', '', cleaned)
    cleaned = re.sub(r'^[ \-_\|\+=]+|[ \-_\|\+=]+$', '', cleaned)
    return cleaned.strip()

def normalize_cctv_name(name):
    upper_name = name.upper().replace("-", "").replace(" ", "")
    if "CCTV5+" in upper_name or "CCTV5PLUS" in upper_name:
        return "CCTV5+"
    match = re.search(r'CCTV(\d+)', upper_name)
    if match:
        return f"CCTV{match.group(1)}"
    return name

def get_channel_group(std_name, is_4k_8k):
    is_cctv = "CCTV" in std_name.upper()
    is_ws = "卫视" in std_name
    is_sd = "山东" in std_name
    
    # 4K频道组规范：只放央视、地方卫视的4K/8K源 (要求7)
    if is_4k_8k and (is_cctv or is_ws): return "4K频道"
    if is_cctv: return "央视频道"
    if is_ws: return "地方卫视"
    if is_sd: return "山东频道"
    if any(x in std_name for x in ["港", "澳", "台", "HBO", "PHOENIX", "凤凰"]): return "港澳台"
    if any(x in std_name for x in ["电影", "影院", "剧场", "影视"]): return "影视频道"
    if any(x in std_name for x in ["纪录", "纪实", "探索", "国家地理"]): return "纪录纪实"
    if any(x in std_name for x in ["动漫", "少儿", "卡通", "儿童"]): return "少儿动画"
    if any(x in std_name for x in ["体育", "赛事", "足球", "高尔夫"]): return "体育赛事"
    if any(x in std_name for x in ["音乐", "MV", "演唱会", "歌曲"]): return "歌曲及音乐MV"
    return "综合频道"

def parse_m3u_content(text, name_repo, stats):
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
            # 直播室过滤与 catvod.com 域名拉黑 (要求3)
            if "catvod.com" in url or "直播室" in current_meta["display_name"] or "直播室" in tgroup:
                stats["filtered_blacklist"] += 1
                current_meta = None
                continue
            
            raw_upper = (tid + tname + dname).upper()
            has_4k_label = "4K" in raw_upper or "2160P" in raw_upper
            has_8k_label = "8K" in raw_upper or "4320P" in raw_upper
            
            # 第三步：优先模糊匹配与精细匹配 name.json (要求8：若原标签自带4K/8K则不破坏)
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
                "url": url, "std_name": final_std_name, "has_4k_label": has_4k_label, "has_8k_label": has_8k_label
            })
            parsed_list.append(current_meta)
            current_meta = None
    return parsed_list

def probe_stream(item, timeout=3):
    start_time = time.time()
    try:
        req = urllib.request.Request(item["url"], headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            delay = time.time() - start_time
            chunk = response.read(128000)
            chunk_str = chunk.decode('utf-8', errors='ignore') if chunk else ""
            
            # 从直播流切片头中动态提取精准分辨率宽度
            width = 1920
            resolution_match = re.search(r'RESOLUTION=\d+x(\d+)', chunk_str)
            if resolution_match:
                v_height = int(resolution_match.group(1))
                width = int(v_height * 16 / 9)
            else:
                if item["has_8k_label"]: width = 7680
                elif item["has_4k_label"]: width = 3840
                elif "m3u8" not in item["url"].lower() and len(chunk) < 30000: width = 640
                
            return {
                "valid": True, "delay": delay, "width": width, 
                "is_4k_8k": width >= 3840 or item["has_4k_label"] or item["has_8k_label"], "item": item
            }
    except Exception:
        return {"valid": False, "item": item}

def render_progress_bar(completed, total, quality_count, start_time):
    # 要求10：探测进度严格保持在一行，不刷屏
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
    proc_speed = completed / elapsed if elapsed > 0 else 0
    with print_lock:
        sys.stdout.write(f"\r🔍 进度: [{bar}] {pct:.1f}% | 已检: {completed}/{total} | 速度: {proc_speed:.1f}条/s | 优质源(>=4K): {quality_count}个\033[K")
        sys.stdout.flush()

def get_cctv_sort_key(name):
    num = re.search(r'CCTV(\d+)', name, re.I)
    if num: return int(num.group(1))
    if "CCTV5+" in name.upper(): return 5.5
    return 99

def main():
    start_run_time = time.time()
    logging.info("==============================================")
    logging.info("       IPTV 管道自动化探测清洗任务开始")
    logging.info("==============================================")
    stats = {"total_raw": 0, "filtered_blacklist": 0, "low_res_filtered": 0, "final_count": 0}
    
    if not os.path.exists(SOURCES_JSON_PATH):
        logging.error(f"未找到 sources.json 配置文件，终止运行。")
        return
        
    with open(SOURCES_JSON_PATH, 'r', encoding='utf-8') as f:
        sources_data = json.load(f)
    source_urls = sources_data.get("urls", [])
    if isinstance(source_urls, str): source_urls = [source_urls]
    
    name_repo = load_name_json()
    all_raw_channels = []
    
    # 第一步：全量拉取与粗筛
    for url in set(source_urls):
        try:
            logging.info(f">>> [第一步] 正在提取源: {url}")
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as r:
                m3u_text = r.read().decode('utf-8', errors='ignore')
            channels = parse_m3u_content(m3u_text, name_repo, stats)
            all_raw_channels.extend(channels)
        except Exception as e:
            logging.warning(f"     ⚠️ 数据源拉取失败跳过: {e}")
            
    stats["total_raw"] = len(all_raw_channels) + stats["filtered_blacklist"]
    logging.info(f">>> [第二步] 协议提取完毕，物理总条目: {len(all_raw_channels)} 条")
    
    # 【核心限流优化】：同频道链接在内存中最多只取前6个去测速，其余直接不走网络请求！
    logging.info(">>> [第三步] 正在执行前置漏斗粗筛，精简测速队列...")
    pre_filter_bucket = defaultdict(list)
    for item in all_raw_channels:
        if len(pre_filter_bucket[item["std_name"]]) < 6:
            pre_filter_bucket[item["std_name"]].append(item)
            
    filtered_test_queue = []
    for items in pre_filter_bucket.values():
        filtered_test_queue.extend(items)
        
    logging.info(f">>> [第四步] 测速队列已从 {len(all_raw_channels)} 条重组锐减至 {len(filtered_test_queue)} 条！启动探测...")
    
    probed_results = []
    completed_tasks = 0
    quality_source_count = 0
    probe_start_time = time.time()
    
    # 采用 100 线程高并发进行网络探活
    with ThreadPoolExecutor(max_workers=100) as executor:
        futures = {executor.submit(probe_stream, item): item for item in filtered_test_queue}
        total_tasks = len(futures)
        for future in as_completed(futures):
            res = future.result()
            completed_tasks += 1
            if res["valid"]:
                probed_results.append(res)
                if res["is_4k_8k"]: quality_source_count += 1
            render_progress_bar(completed_tasks, total_tasks, quality_source_count, probe_start_time)
    print("\n")
    logging.info(f">>> 探测完成，可用存活物理源: {len(probed_results)} 条")
    
    # 要求4：多源去重。4k及以上质量最好的保留一个，以下质量最好的保留一个
    dedup_bucket = defaultdict(lambda: {"4k_best": None, "normal_best": None})
    for res in probed_results:
        item = res["item"]
        std_name = item["std_name"]
        if res["is_4k_8k"]:
            current_best = dedup_bucket[std_name]["4k_best"]
            if not current_best or res["delay"] < current_best["delay"]:
                dedup_bucket[std_name]["4k_best"] = res
        else:
            current_best = dedup_bucket[std_name]["normal_best"]
            if not current_best or res["delay"] < current_best["delay"]:
                dedup_bucket[std_name]["normal_best"] = res
                
    final_output_list = []
    for std_name, bucket in dedup_bucket.items():
        is_cctv_or_ws = "CCTV" in std_name.upper() or "卫视" in std_name
        has_output_any = False
        if bucket["4k_best"]:
            final_output_list.append(bucket["4k_best"])
            has_output_any = True
        if bucket["normal_best"]:
            # 要求2：过滤小于720P(宽度1280)低画质。但央视卫视若全灭，低保保留一个
            if bucket["normal_best"]["width"] < 1280:
                if is_cctv_or_ws and not has_output_any:
                    final_output_list.append(bucket["normal_best"])
                else:
                    stats["low_res_filtered"] += 1
            else:
                final_output_list.append(bucket["normal_best"])
                
    stats["final_count"] = len(final_output_list)
    
    # 第五步：执行系统排序权重计算
    def master_sort_key(res_obj):
        item = res_obj["item"]
        std_name = item["std_name"]
        group = get_channel_group(std_name, res_obj["is_4k_8k"])
        group_idx = GROUP_PRIORITY.index(group) if group in GROUP_PRIORITY else 999
        cctv_idx = get_cctv_sort_key(std_name) if group in ["央视频道", "4K频道"] else 999
        return (group_idx, cctv_idx, res_obj["delay"])
        
    final_output_list.sort(key=master_sort_key)
    logging.info(f">>> [第六步] 正在写入最终优化版 {OUTPUT_M3U_PATH} 文件...")
    
    # 第六步：写入输出文件 (合并融入了要求11、12的高级全局时移回看头部)
    with open(OUTPUT_M3U_PATH, "w", encoding="utf-8") as f:
        # 融入你在第12项里提到的标准全局回看参数声明标签，自动契合 TiviMate / APTV
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.cn/e.xml" catchup="append" catchup-source="?playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}"\n')
        
        for res in final_output_list:
            item = res["item"]
            std_name = item["std_name"]
            final_group = get_channel_group(std_name, res["is_4k_8k"])
            
            display_title = std_name
            upper_std = std_name.upper().replace("-", "").replace(" ", "")
            # 央视频道带中文描述规范 (要求5)
            if "CCTV" in upper_std and upper_std in CCTV_DESC_MAP:
                display_title = CCTV_DESC_MAP[upper_std]
                
            # 分组频道不用额外加 4K 描述，保留经过 name.json 匹配过后的纯净形态
            if "CCTV" in display_title:
                formatted_tvg_id = display_title.split(" ")[0].replace("-", "")
            else:
                formatted_tvg_id = std_name
                
            f.write(f'#EXTINF:-1 tvg-id="{formatted_tvg_id}" tvg-name="{formatted_tvg_id}" tvg-logo="{item["logo"]}" group-title="{final_group}",{display_title}\n')
            f.write(f'{item["url"]}\n')
            
    # 要求1：任务总结报告输出
    duration = time.time() - start_run_time
    logging.info("==============================================")
    logging.info("        Crawl 自动化清洗任务总结报告")
    logging.info("==============================================")
    logging.info(f"- 初始捕获源数据总数   : {stats['total_raw']} 条")
    logging.info(f"- 黑名单/直播室过滤数  : {stats['filtered_blacklist']} 条")
    logging.info(f"- 低质量低分辨率过滤数 : {stats['low_res_filtered']} 条")
    logging.info(f"- 最终 tv.m3u 频道总数 : {stats['final_count']} 条")
    logging.info(f"- 任务运行总消耗时长   : {duration:.2f} 秒")
    logging.info("==============================================")

if __name__ == "__main__":
    main()