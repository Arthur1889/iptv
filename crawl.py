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
GROUP_JSON_PATH = os.path.join(CURRENT_DIR, "group", "group_standard.json")  # 最高优先级分组映射文件
CACHE_TXT_PATH = os.path.join(CURRENT_DIR, "sources_cache.txt") # 24小时本地缓存文件路径
OUTPUT_M3U_PATH = os.path.join(CURRENT_DIR, "tv.m3u")
LOG_FILE_PATH = os.path.join(CURRENT_DIR, "crawl.log")

# 配置日志规范并形成文件 (要求1)
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

# 央视频道标准中文描述映射
CCTV_DESC_MAP = {
    "CCTV1": "CCTV-1 综合", "CCTV2": "CCTV-2 财经", "CCTV3": "CCTV-3 综艺",
    "CCTV4": "CCTV-4 中文国际", "CCTV5": "CCTV-5 体育", "CCTV5+": "CCTV-5+ 体育赛事",
    "CCTV6": "CCTV-6 电影", "CCTV7": "CCTV-7 国防军事", "CCTV8": "CCTV-8 电视剧",
    "CCTV9": "CCTV-9 纪录", "CCTV10": "CCTV-10 科教", "CCTV11": "CCTV-11 戏曲",
    "CCTV12": "CCTV-12 社会与法", "CCTV13": "CCTV-13 新闻", "CCTV14": "CCTV-14 少儿",
    "CCTV15": "CCTV-15 音乐", "CCTV16": "CCTV-16 奥林匹克", "CCTV17": "CCTV-17 农业农村",
    "CCTV4K": "CCTV4K 超高清", "CCTV8K": "CCTV8K 超高清",
    "CETV1": "中国教育-1", "CETV2": "中国教育-2", "CETV3": "中国教育-3", "CETV4": "中国教育-4"
}

# 按照最新的第五步调整的精确重排排序优先级权重列表 (第五步)
GROUP_PRIORITY = [
    "4K频道", "央视频道", "地方卫视", "山东频道", "地方频道", 
    "影视频道", "歌曲及音乐MV", "纪录纪实", "娱乐频道", 
    "电视剧直播", "动漫直播", "港澳台", "海外频道", 
    "体育赛事", "少儿频道", "综合频道"
]

PROVINCES = [
    "北京", "上海", "天津", "重庆", "广东", "山东", "浙江", "江苏", "安徽", "福建", 
    "江西", "湖北", "河南", "河北", "山西", "吉林", "辽宁", "广西", "四川", "贵州", 
    "云南", "陕西", "甘肃", "青海", "宁夏", "新疆", "海南", "西藏", "黑龙江", "内蒙古"
]

# 二级城市到省份老家的纠偏映射库 (注：上海、山东在后面会单独拦截成分支频道，其余地级市全部归入地方频道)
CITY_TO_PROVINCE = {
    "绍兴": "浙江", "宁波": "浙江", "温州": "浙江", "金华": "浙江", "台州": "浙江", "嘉兴": "浙江", "湖州": "浙江", "丽水": "浙江", "衢州": "浙江", "舟山": "浙江", "龙泉": "浙江", "萧山": "浙江", "余姚": "浙江", "兰溪": "浙江",
    "涟水": "江苏", "南京": "江苏", "苏州": "江苏", "无锡": "江苏", "常州": "江苏", "南通": "江苏", "扬州": "江苏", "盐城": "江苏", "徐州": "江苏", "淮安": "江苏", "连云港": "江苏", "泰州": "江苏", "宿迁": "江苏", "镇江": "江苏", "江阴": "江苏", "宜兴": "江苏", "溧水": "江苏", "如东": "江苏", "昆山": "江苏",
    "青岛": "山东", "淄博": "山东", "烟台": "山东", "潍坊": "山东", "济宁": "山东", "泰安": "山东", "威海": "山东", "日照": "山东", "临沂": "山东", "德州": "山东", "聊城": "山东", "滨州": "山东", "菏泽": "山东", "莒县": "山东", "章丘": "山东", "商河": "山东", "新泰": "山东", "寿光": "山东", "齐鲁": "山东",
    "深圳": "广东", "广州": "广东", "珠江": "广东", "中山": "广东", "佛山": "广东", "惠州": "广东", "东莞": "广东", "梅州": "广东", "茂名": "广东", "河源": "广东", "潮州": "广东", "汕头": "广东", "江门": "广东", "云浮": "广东", "肇庆": "广东", "揭阳": "广东", "阳江": "广东", "蛇口": "广东", "潮安": "广东", "开平": "广东", "清远": "广东",
    "邢台": "河北", "石家庄": "河北", "唐山": "河北", "保定": "河北", "秦皇岛": "河北", "邯郸": "河北", "张家口": "河北", "衡水": "河北", "沧州": "河北", "廊坊": "河北", "承德": "河北",
    "禹州": "河南", "洛阳": "河南", "郑州": "河南", "商都": "河南", "鹤壁": "河南", "新乡": "河南", "焦作": "河南", "沁阳": "河南", "濮阳": "河南", "安阳": "河南", "许昌": "河南", "周口": "河南", "川汇": "河南", "南阳": "河南", "延津": "河南", "项城": "河南",
    "营山": "四川", "绵阳": "四川", "成都": "四川", "自贡": "四川", "宜宾": "四川", "遂宁": "四川", "乐山": "四川", "广元": "四川", "巴中": "四川", "德阳": "四川", "南充": "四川", "广安": "四川", "眉山": "四川", "雅安": "四川", "泸州": "四川", "攀枝花": "四川", "仪陇": "四川", "内江": "四川", "三峡": "四川",
    "淮北": "安徽", "合肥": "安徽", "滁州": "安徽", "安庆": "安徽", "六安": "安徽", "毫州": "安徽", "亳州": "安徽", "铜陵": "安徽", "宿州": "安徽", "岳西": "安徽", "芜湖": "安徽", "肥西": "安徽", "蚌埠": "安徽", "马鞍山": "安徽", "蒙城": "安徽",
    "福州": "福建", "泉州": "福建", "晋江": "福建", "宁德": "福建", "三明": "福建", "莆田": "福建", "仙游": "福建", "龙岩": "福建", "新罗": "福建", "漳州": "福建", "厦门": "福建",
    "黄石": "湖北", "荆州": "湖北", "荆门": "湖北", "武汉": "湖北", "襄阳": "湖北", "宜昌": "湖北", "十堰": "湖北", "鄂州": "湖北", "随州": "湖北", "孝感": "湖北", "咸宁": "湖北", "赤壁": "湖北", "恩施": "湖北",
    "长沙": "湖南", "株洲": "湖南", "湘潭": "湖南", "衡阳": "湖南", "邵阳": "湖南", "岳阳": "湖南", "常德": "湖南", "张家界": "湖南", "益阳": "湖南", "郴州": "湖南", "永州": "湖南", "怀化": "湖南", "娄底": "湖南", "湘西": "湖南",
    "七台河": "黑龙江", "哈尔滨": "黑龙江", "大庆": "黑龙江", "伊春": "黑龙江", "齐齐哈尔": "黑龙江", "鹤岗": "黑龙江", "牡丹江": "黑龙江",
    "通海": "云南", "昆明": "云南", "丽江": "云南", "楚雄": "云南", "红河": "云南", "蒙自": "云南", "绿春": "云南", "个旧": "云南", "保山": "云南", "玉溪": "云南", "文山": "云南", "版纳": "云南", "普洱": "云南",
    "壶关": "山西", "太原": "山西", "大同": "山西", "阳泉": "山西", "长治": "山西", "晋城": "山西", "朔州": "山西", "晋中": "山西", "运城": "山西", "忻州": "山西", "临汾": "山西", "吕梁": "山西"
}

def get_target_urls():
    if not os.path.exists(SOURCES_JSON_PATH): return []
    try:
        with open(SOURCES_JSON_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
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
    junk_words = [
        r'2160p', r'1080p', r'720p', r'606p', r'576p', r'480p', r'404p', r'360p', 
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

def determine_final_group(std_name, raw_group, is_4k_8k, group_repo):
    name_up = std_name.upper()
    rg = raw_group.strip() if raw_group else ""
    
    drop_list = ["游戏直播", "听书直播", "老年直播", "解说直播", "监控直播", "蜘蛛直播", "zuqiu直播", "咪视界直播", "KK直播", "瑜伽裤直播", "Ai直播", "钓鱼直播", "API随机点播", "直播室"]
    
    # 严格缩进检查，确保这行 if 和下面一行 return 都在 def 内部
    if any(x in rg or x in std_name for x in drop_list):
        return None

    is_cctv = "CCTV" in name_up or "中央台" in name_up or "CGTN" in name_up
    is_ws = "卫视" in name_up and "朝鲜语" not in name_up
    is_df_zone = any(x in name_up for x in PROVINCES) or any(x in name_up for x in CITY_TO_PROVINCE)
    
    if is_4k_8k and (is_cctv or is_ws or is_df_zone): return "4K频道"
# 修改后的代码（直接利用标准字典查询）
    group_from_json = group_repo.get(std_name)
    if group_from_json:
        return group_from_json  # 只有匹配到了才返回，匹配不到则继续向下走 "综合频道")
    # ... 你的后续逻辑 ...

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

    matched_province = None
    for city, province in CITY_TO_PROVINCE.items():
        if city in std_name:
            matched_province = province
            break

    if is_cctv: return "央视频道"
    if is_ws: return "地方卫视"
    
    target_prov = matched_province if matched_province else next((p for p in PROVINCES if p in std_name), None)
    
    if target_prov == "山东": return "山东频道"
    if target_prov == "上海": return "上海频道"
    if target_prov: return "地方频道"

    if any(x in name_up for x in ["港", "澳", "台", "HBO", "PHOENIX", "凤凰", "翡翠台", "明珠台"]): return "港澳台"
    if any(x in name_up for x in ["电影", "影院", "剧场", "影视"]): return "影视频道"
    if any(x in name_up for x in ["纪录", "纪实", "探索"]): return "纪录纪实"
    if any(x in name_up for x in ["动漫", "少儿", "卡通", "儿童"]): return "少儿动画"
    if any(x in name_up for x in ["体育", "赛事", "足球", "五星体育"]): return "体育赛事"
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
                aliases = [str(a).strip() for a in val]
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
    group_repo = load_json_file(GROUP_JSON_PATH)
    
    target_urls = get_target_urls()
    if not target_urls:
        logging.error("未在 sources.json 中扫到有效配置源，终止清洗。")
        return

    # 🌟 第一步：引入24小时本地缓存机制 (24h Cache Engine)
    use_cache = False
    if os.path.exists(CACHE_TXT_PATH):
        file_mtime = os.path.getmtime(CACHE_TXT_PATH)
        # 判断本地缓存文件修改时间是否在 24小时 (86400秒) 以内
        if time.time() - file_mtime < 86400:
            use_cache = True

    all_raw_channels = []

    if use_cache:
        logging.info("-> 💾 命中24小时内本地缓存，正在直接从缓存文件加载源，跳过网络爬取...")
        try:
            with open(CACHE_TXT_PATH, "r", encoding="utf-8") as f:
                cached_text = f.read()
            all_raw_channels = parse_m3u_content(cached_text, name_repo, blacklist_data, stats)
        except Exception as cache_err:
            logging.warning(f"   ⚠️ 读取缓存失败，降级回网络爬取: {cache_err}")
            use_cache = False

    if not use_cache:
        logging.info("-> 🌐 缓存失效或首次运行，启动网络多源并发异步拉取...")
        raw_m3u_contents = []
        for url in set(target_urls):
            try:
                logging.info(f"   拉取远程数据源: {url}")
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=12) as r:
                    text = r.read().decode('utf-8', errors='ignore')
                    raw_m3u_contents.append(text)
            except Exception as e:
                logging.warning(f"   ⚠️ 该远程源拉取受阻跳过: {e}")
        
        # 将合并后的网络原始数据写入本地缓存文件，打上最新的时间戳
        full_merged_text = "\n".join(raw_m3u_contents)
        try:
            with open(CACHE_TXT_PATH, "w", encoding="utf-8") as f:
                f.write(full_merged_text)
            logging.info("-> 💾 成功固化全量数据源到本地缓存文件，有效期24小时。")
        except Exception as save_cache_err:
            logging.warning(f"   ⚠️ 写入本地缓存文件失败: {save_cache_err}")

        all_raw_channels = parse_m3u_content(full_merged_text, name_repo, blacklist_data, stats)

    stats["total_raw"] = len(all_raw_channels) + stats["filtered_blacklist"]
    logging.info(f"-> 【粗筛大池固化】去重后全量明细共计: {len(all_raw_channels)} 条")

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
        group = determine_final_group(std_name, item["raw_group"], res_obj["is_4k_8k"], group_repo)
        if not group: return (9999, 9999, 9999)
        group_idx = GROUP_PRIORITY.index(group) if group in GROUP_PRIORITY else 999
        cctv_idx = get_cctv_sort_key(std_name) if group in ["央视频道", "4K频道"] else 999
        return (group_idx, cctv_idx, res_obj["delay"])

    final_verified_list.sort(key=master_sort_key)

# 找到你代码中原来的 written_count = 0 这一行，将其及其后的所有写入逻辑替换为以下内容：
    written_count = 0
    with open(OUTPUT_M3U_PATH, "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml,http://epg.51zmt.top:12210/e.xml" catchup="append" catchup-source="?playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}"\n')
        
        for res in final_verified_list:
            item = res["item"]
            std_name = item["std_name"]
            # 使用 determine_final_group 确定分组
            final_group = determine_final_group(std_name, item["raw_group"], res["is_4k_8k"], group_repo)
            if not final_group: continue 

            display_title = std_name
            upper_std = std_name.upper().replace("-", "").replace(" ", "")
            if "CCTV" in upper_std and upper_std in CCTV_DESC_MAP:
                display_title = CCTV_DESC_MAP[upper_std]

            # 生成标准的 tvg-id，并用它生成 Logo 地址
            formatted_tvg = display_title.split(" ")[0].replace("-", "") if "CCTV" in display_title else std_name
            base_logo_url = "https://epg.112114.xyz/logo/"
            new_logo_url = f"{base_logo_url}{formatted_tvg}.png"
            
            # 写入 M3U 内容
            f.write(f'#EXTINF:-1 tvg-id="{formatted_tvg}" tvg-name="{formatted_tvg}" tvg-logo="{new_logo_url}" group-title="{final_group}",{display_title}\n')
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
