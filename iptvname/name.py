import requests, re, json, os, sys, time
from collections import defaultdict
from pypinyin import lazy_pinyin
import urllib3

# 1. 环境配置 (总则)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
sys.stdout.reconfigure(encoding='utf-8')
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# 路径定义
M3U_RAW_PATH = os.path.join(CURRENT_DIR, "origin_raw.txt") 
ORIGINAL_JSON_PATH = os.path.join(CURRENT_DIR, "nameoriginal.json") 
OUTPUT_JSON_PATH = os.path.join(CURRENT_DIR, "name.json")
UNMATCHED_PATH = os.path.join(CURRENT_DIR, "unmatched.txt")

# 规则11：本地缓存路径
CACHE_EPG_HOME = os.path.join(CURRENT_DIR, "cache_epg_home.txt")
CACHE_EPG_ALIAS = os.path.join(CURRENT_DIR, "cache_epg_alias.txt")

def is_cache_valid(file_path, days=7):
    """规则11：判断缓存是否在1周内有效"""
    if not os.path.exists(file_path):
        return False
    return (time.time() - os.path.getmtime(file_path)) < (days * 24 * 3600)

def get_content_with_cache(url, cache_path, headers, timeout=8):
    """规则11：周周期下载机制 + 强制超时降级保护"""
    if is_cache_valid(cache_path, days=7):
        print(f"   [本地缓存有效] 直接读取: {os.path.basename(cache_path)}")
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()
    try:
        print(f"   [正在请求网络] {url}...")
        r = requests.get(url, headers=headers, timeout=timeout, verify=False)
        r.raise_for_status()
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(r.text)
        return r.text
    except Exception as e:
        print(f"   ⚠️ 网络连接失败/超时: {e}")
        if os.path.exists(cache_path):
            print(f"   [安全降级] 读取本地旧缓存继续运行...")
            with open(cache_path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

def has_chinese(text):
    """规则1：优先判断中文"""
    if not text: return False
    return any('\u4e00' <= char <= '\u9fa5' for char in text)

def clean_alias(text):
    """规则2 & 8：清洗特定后缀、HTML标签、消除末尾空格"""
    if not text: return ""
    text = str(text)
    # 规则8：清洗所有HTML标签和 </br>
    text = re.sub(r'<[^>]+>', '', text)
    # 规则2：移除特定规格噪声
    text = re.sub(r'[\(\（\[\【\《].*?[\)\）\]\】\局\技巧\设\》]', '', text)
    junk = [
        r'\.cn@SD', r'\.cn@HD', r'\.hk@SD', r'\.png$',
        r'2160p', r'1080p', r'720p', r'576p', r'576i', r'540p', r'480p', r'360p', r'180p',
        r'\[Not 24/7\]', r'\[Geo-blocked\]'
    ]
    for p in junk:
        text = re.sub(p, '', text, flags=re.I)
    return text.replace('-', '').strip()

def to_pure_pinyin(text):
    """规则3 & 4 & 6：去掉TV、空格、转化为大写纯拼音"""
    if not text: return ""
    text = clean_alias(text).replace(' ', '')
    text = re.sub(r'TV|Television|Satellite', '', text, flags=re.I)
    try:
        py_list = [str(x) for x in lazy_pinyin(text)]
        return "".join(py_list).upper()
    except Exception:
        return ""

def run_pipeline():
    start_time = time.time()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    # 核心映射表
    name_map = defaultdict(set)
    
    # 全局多轨检索树（哈希表）
    search_index = {}                 # 全小写明文 -> 标准名
    raw_search_index = {}             # 未经clean_alias的原始明文 -> 标准名
    pinyin_index = defaultdict(list)  # 大写纯拼音 -> [标准名列表]

    # 规则9：指定的6种高频衍生后缀
    RULE_9_SUFFIXES = ["HD", ".cn@SD", ".cn@HD", " (2160p)", " (720p)", " (1080p)"]

    def register(std_name, raw_value):
        """规则5：滚动对撞反哺器"""
        if not raw_value or not std_name: return
        
        name_map[std_name].add(raw_value)
        
        raw_lower = str(raw_value).replace(' ', '').replace('-', '').lower()
        if raw_lower:
            raw_search_index[raw_lower] = std_name
            
        cleaned = clean_alias(raw_value)
        if cleaned:
            search_index[cleaned.lower()] = std_name
            name_map[std_name].add(cleaned)
            
            py = to_pure_pinyin(cleaned)
            if py and std_name not in pinyin_index[py]:
                pinyin_index[py].append(std_name)

    # 规则10：语义多向对撞库
    semantic_compensations = {
        "CCTV1": "CCTV1综合", "CCTV2": "CCTV2财经", "CCTV3": "CCTV3综艺", "CCTV4": "CCTV4中文国际",
        "CCTV5": "CCTV5体育", "CCTV6": "CCTV6电影", "CCTV7": "CCTV7国防军事", "CCTV8": "CCTV8电视剧",
        "CCTV9": "CCTV9纪录", "CCTV10": "CCTV10科教", "CCTV11": "CCTV11戏曲", "CCTV12": "CCTV12社会与法",
        "CCTV13": "CCTV13新闻", "CCTV14": "CCTV14少儿", "CCTV15": "CCTV15音乐", "CCTV16": "CCTV16奥林匹克",
        "CCTV17": "CCTV17农业农村", "CCTV5+": "CCTV5+体育赛事",
        "HUNANTV": "湖南卫视", "ZHEJIANGTV": "浙江卫视", "DRAGONTV": "东方卫视", "JIANGSUTV": "江苏卫视",
        "ANHUITV": "安徽卫视", "BEIJINGTV": "北京卫视", "GUANGDONGTV": "广东卫视", "SHENZHENTV": "深圳卫视"
    }

    try:
        # --- 第一步：抓取/解析标准名库 ---
        print(">>> [1/4] 正在抓取并全量提取标准名称基库...")
        html_home = get_content_with_cache("https://epg.112114.xyz", CACHE_EPG_HOME, headers)
        
        stds = re.findall(r'title="([^"]+)"', html_home) if html_home else []
        if html_home:
            stds += re.findall(r'>([\u4e00-\u9fa5]{2,12}(?:\d+[\+]?)?)<', html_home)
            stds += re.findall(r'[\u4e00-\u9fa5]{2,10}(?:\d+[\+]?|超高?清)?', html_home)
        
        for s in set(stds):
            s_std = clean_alias(s)
            if s_std and not s_std.startswith("http") and len(s_std) > 1:
                register(s_std, s_std)
                # 建立基础别名池
                for suf in RULE_9_SUFFIXES:
                    register(s_std, f"{s_std}{suf}")

        # --- 第二步：提取线上别名映射表 ---
        print(">>> [2/4] 正在拉取线上别名数据库...")
        html_alias = get_content_with_cache("https://epg.112114.xyz/alias", CACHE_EPG_ALIAS, headers)
        
        if html_alias:
            for line in html_alias.splitlines():
                line = line.strip()
                if not line or ("-->" not in line and ":" not in line): continue
                parts = re.split(r'-->|:', line)
                if len(parts) >= 2:
                    p1, p2 = parts[0].strip(), parts[1].strip()
                    c1, c2 = clean_alias(p1), clean_alias(p2)
                    
                    target = None
                    for check_key in [c1.lower(), p1.replace(' ','').lower(), c2.lower(), p2.replace(' ','').lower()]:
                        target = search_index.get(check_key) or raw_search_index.get(check_key)
                        if target: break
                    
                    if not target:
                        py1, py2 = to_pure_pinyin(c1), to_pure_pinyin(c2)
                        if py1 in pinyin_index: target = pinyin_index[py1][0]
                        elif py2 in pinyin_index: target = pinyin_index[py2][0]
                    
                    final_std = target if target else (c1 if c1 else c2)
                    if final_std:
                        register(final_std, p1)
                        register(final_std, p2)

        # --- 第三步：识别合并本地 nameoriginal.json ---
        if os.path.exists(ORIGINAL_JSON_PATH):
            print(f">>> [3/4] 正在深度融合本地 {os.path.basename(ORIGINAL_JSON_PATH)}...")
            with open(ORIGINAL_JSON_PATH, 'r', encoding='utf-8') as f:
                orig_data = json.load(f)
                for k, v in orig_data.items():
                    k_clean = clean_alias(k)
                    
                    target_std = search_index.get(k_clean.lower()) or raw_search_index.get(k.replace(' ','').lower())
                    if not target_std:
                        py_k = to_pure_pinyin(k_clean)
                        if py_k in pinyin_index: target_std = pinyin_index[py_k][0]
                    if not target_std:
                        target_std = k_clean
                        
                    register(target_std, k)
                    a_list = v.split(',') if isinstance(v, str) else v
                    for a in a_list:
                        register(target_std, a)

        # --- 第四步：抓取需识别数据库源列表 (cn.m3u) ---
        print(">>> [4/4] 正在下载并多轮滚动匹配待识别 M3U 源列表...")
        m3u_text = get_content_with_cache(
            "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u", 
            M3U_RAW_PATH, 
            headers,
            timeout=12
        )

        stats = {"total": 0, "matched": 0}
        unmatched = set()

        if m3u_text:
            for line in m3u_text.splitlines():
                if line.startswith("#EXTINF:"):
                    stats["total"] += 1
                    tid = re.search(r'tvg-id="([^"]*)"', line).group(1) if 'tvg-id="' in line else ""
                    tname = re.search(r'tvg-name="([^"]*)"', line).group(1) if 'tvg-name="' in line else ""
                    dname = line.split(",")[-1].strip()

                    best_source = dname if has_chinese(dname) else tid if has_chinese(tid) else tname if has_chinese(tname) else (tid or tname or dname)
                    target_std = None
                    cleaned_best = clean_alias(best_source)
                    raw_no_space = str(best_source).replace(' ', '').replace('-', '').lower()

                    # 第一轮：明文碰撞
                    if cleaned_best:
                        target_std = search_index.get(cleaned_best.lower())
                    if not target_std:
                        target_std = raw_search_index.get(raw_no_space)

                    # 第二轮：语义补偿
                    if not target_std and cleaned_best:
                        upper_no_space = cleaned_best.replace(" ", "").upper()
                        if upper_no_space in semantic_compensations:
                            target_std = search_index.get(semantic_compensations[upper_no_space].lower())

                    # 第三/四轮：大写拼音哈希对撞
                    if not target_std:
                        source_py = to_pure_pinyin(best_source)
                        if source_py and source_py in pinyin_index:
                            matched_stds = pinyin_index[source_py]
                            target_std = matched_stds[0]
                            for candidate in matched_stds:
                                if has_chinese(candidate):
                                    target_std = candidate
                                    break

                    if target_std:
                        for info in [tid, tname, dname]:
                            if info: register(target_std, info)
                        stats["matched"] += 1
                    else:
                        unmatched.add(dname)

        # --- 第五步：最终输出整理（修复：强制保护规则9别名不被洗掉） ---
        final_json = {}
        for k, v in name_map.items():
            k_clean = clean_alias(k)
            if k_clean and (has_chinese(k_clean) or len(k_clean) > 2):
                # 1. 基础别名清洗（过滤掉变回自身的冗余词）
                v_clean = {clean_alias(a) for a in v if clean_alias(a) and clean_alias(a) != k_clean}
                
                # 2. 【核心修复】强制把规则9指定的 6 种衍生后缀灌入别名集合中（绕过清洗器）
                for suf in RULE_9_SUFFIXES:
                    v_clean.add(f"{k_clean}{suf}")
                
                # 排序并转化为字符串保存
                final_json[k_clean] = ",".join(sorted(list(v_clean)))
        
        with open(OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(final_json, f, indent=2, ensure_ascii=False)
        
        with open(UNMATCHED_PATH, 'w', encoding='utf-8') as f:
            f.write("\n".join(sorted(list(unmatched))))

        # 任务报告
        duration = time.time() - start_time
        print(f"\n==========================================")
        print(f"        name.py 自动化任务报告")
        print(f"==========================================")
        print(f"- 需识别别名总数 : {stats['total']}")
        print(f"- 成功识别数量   : {stats['matched']}")
        print(f"- 未能识别数量   : {len(unmatched)}")
        print(f"- 任务执行总耗时 : {duration:.2f} 秒")
        print(f"==========================================\n")

    except Exception as e:
        print(f"\n❌ 脚本执行遇到严重异常: {e}")

if __name__ == "__main__":
    run_pipeline()