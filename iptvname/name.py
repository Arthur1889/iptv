import requests, re, json, os, sys, time
from collections import defaultdict
from pypinyin import lazy_pinyin
import urllib3

# 1. 环境配置
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
sys.stdout.reconfigure(encoding='utf-8')
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# 路径定义
M3U_RAW_PATH = os.path.join(CURRENT_DIR, "origin_raw.txt") 
ORIGINAL_JSON_PATH = os.path.join(CURRENT_DIR, "nameoriginal.json") 
OUTPUT_JSON_PATH = os.path.join(CURRENT_DIR, "name.json")
UNMATCHED_PATH = os.path.join(CURRENT_DIR, "unmatched.txt")

def has_chinese(text):
    """规则1：判断是否含中文"""
    if not text: return False
    return any('\u4e00' <= char <= '\u9fa5' for char in text)

def clean_alias(text):
    """规则2 & 8：彻底粉碎HTML标签、规格括号、特定字符及末尾空格"""
    if not text: return ""
    
    # A. 规则8：强力清除所有 HTML 标签 (彻底干掉 </br>)
    text = re.sub(r'<[^>]+>', '', text)
    
    # B. 规则2：移除所有规格括号及其内容
    text = re.sub(r'[\(\（\[\【\《].*?[\)\）\]\】\》]', '', text)
    
    # C. 规则2：移除特定的后缀杂质
    junk = [
        r'\.cn@SD', r'\.cn@HD', r'\.hk@SD', r'\.png$',
        r'2160p', r'1080p', r'720p', r'576p', r'576i', r'540p', r'480p', r'360p', r'180p',
        r'\[Not 24/7\]', r'\[Geo-blocked\]'
    ]
    for p in junk:
        text = re.sub(p, '', text, flags=re.I)
    
    # D. 规则2：去掉横杠并强制 strip 去除末尾空格
    text = text.replace('-', '').strip()
    
    return text

def to_upper_pinyin(text):
    """规则3&4&6：语义翻译并转为大写拼音进行碰撞"""
    if not text: return ""
    # 规则3：语义翻译
    text = re.sub(r'(?i)Satellite', '卫视', text)
    text = re.sub(r'(?i)Generalist|Comprehensive|News', '综合', text)
    text = re.sub(r'(?i)TV|Television', '', text) 
    
    # 清洗后去掉所有内部空格
    pure = clean_alias(text).replace(' ', '')
    # 规则4：去掉常见频道后缀以增加匹配成功率
    pure = re.sub(r'卫视$|台$|频道$|综合$', '', pure)
    return "".join(lazy_pinyin(pure)).upper()

def run_pipeline():
    start_time = time.time()
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    name_map = defaultdict(set)
    search_index = {} # 用于存放识别键到标准名的映射
    stats = {"m3u_total": 0, "m3u_matched": 0}

    def update_index(std, val):
        """规则5：滚动实时更新索引库"""
        if not val: return
        # 存入清洗后的小写名 (规则6)
        search_index[clean_alias(val).lower()] = std
        # 存入大写拼音 (规则4)
        search_index[to_upper_pinyin(val)] = std

    try:
        # --- 第一步：抓取标准名 ---
        print(">>> [1/4] 抓取并清洗标准名...")
        r_home = requests.get("https://epg.112114.xyz", headers=headers, timeout=20, verify=False)
        stds = re.findall(r'title="([^"]+)"', r_home.text)
        stds += re.findall(r'[\u4e00-\u9fa5]{2,10}(?:\d+[\+]?|超高?清)?', r_home.text)
        for s in set(stds):
            if s and not s.startswith("http"):
                # 规则8：标准名进入前必须清洗
                s_std = clean_alias(s)
                if s_std:
                    name_map[s_std].add(s_std)
                    update_index(s_std, s_std)

        # --- 第二步：提取线上别名 ---
        print(">>> [2/4] 抓取线上别名映射...")
        r_alias = requests.get("https://epg.112114.xyz/alias", headers=headers, timeout=20, verify=False)
        for line in r_alias.text.splitlines():
            parts = re.split(r'-->|:', line)
            if len(parts) >= 2:
                # 识别标准名和别名
                std_raw = parts[1].strip() if '-->' in line else parts[0].strip()
                alias_raw = parts[0].strip() if '-->' in line else parts[1].strip()
                std = clean_alias(std_raw)
                if std:
                    name_map[std].add(clean_alias(alias_raw))
                    update_index(std, alias_raw)

        # --- 第三步：解析本地 nameoriginal.json ---
        if os.path.exists(ORIGINAL_JSON_PATH):
            print(f">>> [3/4] 合并本地 {os.path.basename(ORIGINAL_JSON_PATH)}...")
            with open(ORIGINAL_JSON_PATH, 'r', encoding='utf-8') as f:
                orig_data = json.load(f)
                for std_raw, aliases in orig_data.items():
                    std = clean_alias(std_raw)
                    if not std: continue
                    a_list = aliases.split(',') if isinstance(aliases, str) else aliases
                    for a in a_list:
                        a_clean = clean_alias(a)
                        name_map[std].add(a_clean)
                        update_index(std, a)

        # --- 第四步：识别 iptv-org 数据库 ---
        print(">>> [4/4] 正在识别 iptv-org 源列表并回填...")
        r_m3u = requests.get("https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u", headers=headers, timeout=30, verify=False)
        with open(M3U_RAW_PATH, "w", encoding="utf-8") as f:
            f.write(r_m3u.text)

        unmatched = set()
        for line in r_m3u.text.splitlines():
            if line.startswith("#EXTINF:"):
                stats["m3u_total"] += 1
                tid_m = re.search(r'tvg-id="([^"]*)"', line)
                tname_m = re.search(r'tvg-name="([^"]*)"', line)
                tid, tname = (tid_m.group(1) if tid_m else ""), (tname_m.group(1) if tname_m else "")
                dname = line.split(",")[-1].strip()

                # 规则1：优先级判断
                best_raw = ""
                if has_chinese(dname): best_raw = dname
                elif has_chinese(tid): best_raw = tid
                elif has_chinese(tname): best_raw = tname
                else: best_raw = tid if tid else tname if tname else dname

                # 执行匹配 (第一轮清洗匹配 + 第二轮拼音匹配)
                target_std = search_index.get(clean_alias(best_raw).lower())
                if not target_std:
                    target_std = search_index.get(to_upper_pinyin(best_raw))

                if target_std:
                    # 规则7：匹配成功，回填所有原始字段（清洗后的）
                    for info in [tid, tname, dname]:
                        if info:
                            info_clean = clean_alias(info)
                            if info_clean: name_map[target_std].add(info_clean)
                    
                    # 规则5：滚动更新
                    update_index(target_std, dname)
                    stats["m3u_matched"] += 1
                else:
                    unmatched.add(dname)

        # 整理输出
        final_json = {k: ",".join(sorted(list(v))) for k, v in name_map.items() if k}
        with open(OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(final_json, f, indent=2, ensure_ascii=False)
        
        with open(UNMATCHED_PATH, 'w', encoding='utf-8') as f:
            f.write("\n".join(sorted(list(unmatched))))

        duration = time.time() - start_time
        print(f"\n✅ 任务报告:\n- 别名总数: {stats['m3u_total']}\n- 识别数量: {stats['m3u_matched']}\n- 未识别数: {len(unmatched)}\n- 总计耗时: {duration:.2f}s")

    except Exception as e:
        print(f"\n❌ 执行失败: {e}")

if __name__ == "__main__":
    run_pipeline()