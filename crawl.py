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

# 1. 自动适配多系统环境
SYSTEM_OS = platform.system().lower()
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# 路径定义
SOURCES_JSON_PATH = os.path.join(CURRENT_DIR, "sources.json")
NAME_JSON_PATH = os.path.join(CURRENT_DIR, "name.json")
OUTPUT_M3U_PATH = os.path.join(CURRENT_DIR, "tv.m3u")       # 要求 9：指定为 tv.m3u
LOG_FILE_PATH = os.path.join(CURRENT_DIR, "crawl.log")

# 2. 规范化配置 Log 日志 (要求 1)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE_PATH, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# 进度条专属全局锁与刷新控制（要求 10：防刷屏）
print_lock = Lock()
last_print_time = 0

# 静态映射：央视中文描述映射表
CCTV_DESC_MAP = {
    "CCTV1": "CCTV-1 综合", "CCTV2": "CCTV-2 财经", "CCTV3": "CCTV-3 综艺", "CCTV4": "CCTV-4 中文国际",
    "CCTV5": "CCTV-5 体育", "CCTV5+": "CCTV-5+ 体育赛事", "CCTV6": "CCTV-6 电影", "CCTV7": "CCTV-7 国防军事",
    "CCTV8": "CCTV-8 电视剧", "CCTV9": "CCTV-9 纪录", "CCTV10": "CCTV10 科教", "CCTV11": "CCTV11 戏曲",
    "CCTV12": "CCTV12 社会与法", "CCTV13": "CCTV13 新闻", "CCTV14": "CCTV14 少儿", "CCTV15": "CCTV15 音乐",
    "CCTV16": "CCTV16 奥林匹克", "CCTV17": "CCTV17 农业农村"
}

# 排序分组权重表 (第五步)
GROUP_PRIORITY = [
    "4K频道", "央视频道", "地方卫视", "港澳台", "山东频道", "数字频道", "影视频道", 
    "纪录纪实", "娱乐频道", "少儿动画", "体育赛事", "歌曲及音乐MV", "外语频道", "综合频道"
]

def load_name_json():
    """第三步：加载别名反射库"""
    if os.path.exists(NAME_JSON_PATH):
        try:
            with open(NAME_JSON_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"加载 name.json 失败: {e}")
    return {}

def clean_display_name(name):
    """要求 6：清除名称后方的各种杂质规格尾缀"""
    if not name: return ""
    junk = [r'2160p', r'1080p', r'720p', r'576p', r'576i', r'hd', r'sd', r'\[Not 24/7\]', r'\[Geo-blocked\]']
    cleaned = name
    for pattern in junk:
        cleaned = re.sub(pattern, '', cleaned, flags=re.I)
    # 去掉一些前后多余的破折号或空格
    cleaned = re.sub(r'^[ \-_]+|[ \-_]+$', '', cleaned)
    return cleaned.strip()

def get_channel_group(std_name, is_4k_8k):
    """第五步 & 要求 7：动态智能分组逻辑"""
    is_cctv = "CCTV" in std_name.upper()
    is_ws = "卫视" in std_name
    is_sd = "山东" in std_name
    
    # 要求7：4K组只放央视、地方卫视的4K/8K，8K直接保留放里面
    if is_4k_8k and (is_cctv or is_ws):
        return "4K频道"
    if is_cctv:
        return "央视频道"
    if is_ws:
        return "地方卫视"
    if is_sd:
        return "山东频道"
    if any(x in std_name for x in ["港", "澳", "台", "HBO", "PHOENIX", "凤凰"]):
        return "港澳台"
    if any(x in std_name for x in ["电影", "影院", "剧场", "影视"]):
        return "影视频道"
    if any(x in std_name for x in ["纪录", "纪实", "探索", "国家地理"]):
        return "纪录纪实"
    if any(x in std_name for x in ["动漫", "少儿", "卡通", "儿童"]):
        return "少儿动画"
    if any(x in std_name for x in ["体育", "赛事", "足球", "高尔夫"]):
        return "体育赛事"
    if any(x in std_name for x in ["音乐", "MV", "演唱会", "歌曲"]):
        return "歌曲及音乐MV"
    
    return "综合频道"

def parse_m3u_content(text, name_repo, stats):
    """第一、二、三步：提取、匹配并过滤初步黑名单"""
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
                "raw_id": tid, "raw_name": tname, "logo": tlogo, 
                "raw_group": tgroup, "display_name": dname
            }
        elif line.startswith("http") and current_meta:
            url = line
            # 要求 3：带有 catvod.com 的源和直播室一律直接过滤
            if "catvod.com" in url or "直播室" in current_meta["display_name"]:
                stats["filtered_blacklist"] += 1
                current_meta = None
                continue
                
            # 第三步：逆向匹配 name.json
            matched_std = None
            dname_clean = clean_display_name(current_meta["display_name"])
            tname_clean = clean_display_name(current_meta["raw_name"])
            
            for key, val in name_repo.items():
                aliases = [a.strip() for a in val.split(",")]
                if dname_clean in aliases or tname_clean in aliases or dname_clean == key:
                    matched_std = key
                    break
            
            final_std_name = matched_std if matched_std else dname_clean
            
            # 要求 8：检测原始文本（含未清洗的名）中是否本身带有4K/8K物理标识
            raw_upper = (tid + tname + dname).upper()
            has_4k_label = "4K" in raw_upper
            has_8k_label = "8K" in raw_upper
            
            current_meta.update({
                "url": url,
                "std_name": final_std_name,
                "has_4k_label": has_4k_label,
                "has_8k_label": has_8k_label
            })
            parsed_list.append(current_meta)
            current_meta = None
            
    return parsed_list

def probe_stream(item, timeout=3):
    """第四步 & 要求 2：高级流媒体测速与真实分辨率特征嗅探"""
    start_time = time.time()
    try:
        req = urllib.request.Request(item["url"], headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            # 获取头部建立连接时间 (速度)
            delay = time.time() - start_time
            
            # 尝试读取前 256KB 数据块，嗅探分辨率/码率特征
            chunk = response.read(256000)
            chunk_str = chunk.decode('utf-8', errors='ignore') if chunk else ""
            
            # 分辨率判定逻辑 (要求 2)
            # 默认假设是 1080P (宽度 1920)
            width = 1920
            
            # 如果是嵌套 M3U8，分析内部子流的分辨率标签
            resolution_match = re.search(r'RESOLUTION=\d+x(\d+)', chunk_str)
            if resolution_match:
                v_height = int(resolution_match.group(1))
                width = int(v_height * 16 / 9)
            else:
                # 模糊策略：根据速度和原始打标辅助判定
                if item["has_8k_label"]: width = 7680
                elif item["has_4k_label"]: width = 3840
                elif "m3u8" not in item["url"].lower() and len(chunk) < 50000:
                    # 读取数据过慢或切片过小，判定为低清源
                    width = 640
                    
            return {
                "valid": True, "delay": delay, "width": width,
                "is_4k_8k": width >= 3840, "item": item
            }
    except Exception:
        return {"valid": False, "item": item}

def render_progress_bar(completed, total, speed, quality_count, start_time):
    """要求 1 & 要求 10：高防刷性能控制台进度条"""
    global last_print_time
    now = time.time()
    # 限制刷新频率（每0.1秒或100%时才打印一次），防止多线程疯狂回刷造成闪烁跳行
    if now - last_print_time < 0.1 and completed < total:
        return
    last_print_time = now
    
    if total == 0: return
    pct = (completed / total) * 100
    bar_len = 25
    filled = int(bar_len * completed // total)
    bar = '█' * filled + '-' * (bar_len - filled)
    
    elapsed = now - start_time
    # 动态估算探测速度 (条/秒)
    proc_speed = completed / elapsed if elapsed > 0 else 0
    
    with print_lock:
        # \r 强制回车符覆盖整行，\033[K 清除行尾，多系统下绝对不跳行刷屏
        sys.stdout.write(f"\r🔍 进度: [{bar}] {pct:.1f}% | 已检: {completed}/{total} | 耗时: {elapsed:.1f}s | 速度: {proc_speed:.1f}条/s | 4K/8K优质源: {quality_count}个\033[K")
        sys.stdout.flush()

def get_cctv_sort_key(name):
    """要求 5：央视频道 1-17 精准纯序数字排列"""
    num = re.search(r'CCTV(\d+)', name, re.I)
    if num:
        return int(num.group(1))
    if "CCTV5+" in name.upper(): return 5.5
    return 99

def main():
    start_run_time = time.time()
    logging.info("==============================================")
    logging.info("       IPTV 管道自动化探测清洗任务开始")
    logging.info("==============================================")
    
    stats = {"total_raw": 0, "filtered_blacklist": 0, "low_res_filtered": 0, "final_count": 0}
    
    # 第一步：提取 sources.json
    if not os.path.exists(SOURCES_JSON_PATH):
        logging.error(f"未找到 sources.json 配置文件，终止运行。")
        return
        
    with open(SOURCES_JSON_PATH, 'r', encoding='utf-8') as f:
        sources_data = json.load(f)
    
    source_urls = sources_data.get("urls", [])
    if isinstance(source_urls, str): source_urls = [source_urls]
    
    name_repo = load_name_json()
    all_raw_channels = []
    
    # 拉取并合并去重
    for url in set(source_urls):
        try:
            logging.info(f">>> [第一步] 正在同步并拉取远端数据源: {url}")
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as r:
                m3u_text = r.read().decode('utf-8', errors='ignore')
                channels = parse_m3u_content(m3u_text, name_repo, stats)
                all_raw_channels.extend(channels)
        except Exception as e:
            logging.warning(f"     ⚠️ 数据源拉取失败跳过: {e}")

    stats["total_raw"] = len(all_raw_channels) + stats["filtered_blacklist"]
    logging.info(f">>> [第二步] 提取解析完毕，共捕获可用物理条目: {len(all_raw_channels)} 条")

    # 第四步：多线程并发大网探测
    logging.info(">>> [第四步] 开启多路线程进行流媒体画质与延迟测试（请稍候）...")
    probed_results = []
    completed_tasks = 0
    quality_source_count = 0
    probe_start_time = time.time()
    
    # 100路高并发执行
    with ThreadPoolExecutor(max_workers=100) as executor:
        futures = {executor.submit(probe_stream, item): item for item in all_raw_channels}
        total_tasks = len(futures)
        
        for future in as_completed(futures):
            res = future.result()
            completed_tasks += 1
            if res["valid"]:
                probed_results.append(res)
                if res["is_4k_8k"]:
                    quality_source_count += 1
            
            render_progress_bar(completed_tasks, total_tasks, 0, quality_source_count, probe_start_time)
                
    print("\n") # 进度条安全平滑换行，不破坏终端格式
    logging.info(f">>> 链路并发探测完成，测速存活物理源: {len(probed_results)} 条")

    # 要求 4 & 要求 2：基于策略对频道进行双档独立去重
    # 结构：{ 频道标准名: { "4k_best": res, "normal_best": res } }
    dedup_bucket = defaultdict(lambda: {"4k_best": None, "normal_best": None})
    
    for res in probed_results:
        item = res["item"]
        std_name = item["std_name"]
        
        if res["is_4k_8k"]:
            # 4K及以上质量最好的保留一个（延迟最低）
            current_best = dedup_bucket[std_name]["4k_best"]
            if not current_best or res["delay"] < current_best["delay"]:
                dedup_bucket[std_name]["4k_best"] = res
        else:
            # 4K以下质量中响应速度最快的保留一个
            current_best = dedup_bucket[std_name]["normal_best"]
            if not current_best or res["delay"] < current_best["delay"]:
                dedup_bucket[std_name]["normal_best"] = res

    # 构建过滤输出集
    final_output_list = []
    
    for std_name, bucket in dedup_bucket.items():
        is_cctv_or_ws = "CCTV" in std_name.upper() or "卫视" in std_name
        has_output_any = False
        
        # 1. 优先提取 4K/8K 画质档
        if bucket["4k_best"]:
            final_output_list.append(bucket["4k_best"])
            has_output_any = True
            
        # 2. 提取普通画质档 (要求 2：画质需>=720P 即宽度>=1280)
        if bucket["normal_best"]:
            if bucket["normal_best"]["width"] < 1280:
                if is_cctv_or_ws and not has_output_any: 
                    # 央视和地方卫视如果只有720P及以下分辨率，强制保留至少一个保底
                    final_output_list.append(bucket["normal_best"])
                else:
                    # 其它普通频道低于720P则直接过滤淘汰
                    stats["low_res_filtered"] += 1
            else:
                final_output_list.append(bucket["normal_best"])
        
    stats["final_count"] = len(final_output_list)

    # 第五步：多级无缝动态权重排序
    def master_sort_key(res_obj):
        item = res_obj["item"]
        std_name = item["std_name"]
        
        # 1. 计算分组所属与权重
        group = get_channel_group(std_name, res_obj["is_4k_8k"])
        group_idx = GROUP_PRIORITY.index(group) if group in GROUP_PRIORITY else 999
        
        # 2. 央视频道内部阶梯排序 (要求 5)
        cctv_idx = get_cctv_sort_key(std_name) if group in ["央视频道", "4K频道"] else 999
        
        return (group_idx, cctv_idx, res_obj["delay"])

    final_output_list.sort(key=master_sort_key)

    # 第六步：格式化输出规范的 M3U8 文件
    logging.info(f">>> [第六步] 正在生成标准的 M3U8 扩展标签协议文件...")
    with open(OUTPUT_M3U_PATH, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        
        for res in final_output_list:
            item = res["item"]
            std_name = item["std_name"]
            final_group = get_channel_group(std_name, res["is_4k_8k"])
            
            # 要求 6：默认名称去掉HD等字眼
            display_title = clean_display_name(std_name)
            
            # 央视特殊化增强映射描述
            upper_std = std_name.upper().replace("-", "").replace(" ", "")
            if "CCTV" in upper_std and upper_std in CCTV_DESC_MAP:
                display_title = CCTV_DESC_MAP[upper_std]
            
            # 要求 8 & 要求 7：如果原始数据包含4K/8K标识，非4K组则予以保留，4K分组内的频道则不加额外描述
            if final_group != "4K频道":
                if item["has_8k_label"] and "8K" not in display_title.upper():
                    display_title += " 8K"
                elif item["has_4k_label"] and "4K" not in display_title.upper():
                    display_title += " 4K"
                
            # 标准 M3U 协议拼装落地
            f.write(f'#EXTINF:-1 tvg-id="{std_name}" tvg-name="{std_name}" tvg-logo="{item["logo"]}" group-title="{final_group}",{display_title}\n')
            f.write(f'{item["url"]}\n')

    # 要求 1：打印并输出最终任务结项报告
    duration = time.time() - start_run_time
    logging.info("==============================================")
    logging.info("        Crawl 自动化清洗任务总结报告")
    logging.info("==============================================")
    logging.info(f"- 初始捕获源数据总数   : {stats['total_raw']} 条")
    logging.info(f"- 黑名单/直播室过滤数  : {stats['filtered_blacklist']} 条")
    logging.info(f"- 低质量低分辨率过滤数 : {stats['low_res_filtered']} 条")
    logging.info(f"- 最终 tv.m3u 频道数   : {stats['final_count']} 条")
    logging.info(f"- 任务运行总消耗时长   : {duration:.2f} 秒")
    logging.info(f"- 系统运行日志安全存入 : {os.path.basename(LOG_FILE_PATH)}")
    logging.info(f"- 终极清洗合规M3U成果  : {os.path.basename(OUTPUT_M3U_PATH)}")
    logging.info("==============================================")

if __name__ == "__main__":
    main()