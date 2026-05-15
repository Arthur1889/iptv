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
    """规则 1：判断含中文"""
    if not text: return False
    return any('\u4e00' <= char <= '\u9fa5' for char in text)

def clean_alias(text):
    """规则 2 & 8：彻底除污（HTML标签、规格、末尾空格）"""
    if not text: return ""
    # 规则 8：粉碎 HTML 标签
    text = re.sub(r'<[^>]+>', '', str(text))
    # 规则 2：清理规格后缀和括号
    # 使用非贪婪匹配处理成对括号：(...) [...] 【...】
    text = re.sub(r'[\(\（\[\【\《].*?[\)\）\]\】\》]', '', text)
    junk = [
        r'\.cn@SD', r'\.cn@HD', r'\.hk@SD', r'\.png$',
        r'2160p', r'1080p', r'720p', r'576p', r'576i', r'540p', r'480p', r'360p', r'180p',
        r'\[Not 24/7\]', r'\[Geo-blocked\]'
    ]
    for p in junk:
        text = re.sub(p, '', text, flags=re.I)
    # 规则 2：去掉横杠、下划线并严格去掉末尾空格
    text = text.replace('-', '').replace('_', '').strip()
    return text

def get_pinyin_variants(text):
    """规则 3, 4, 6, 10：增强语义补偿及拼音转换"""
    if not text: return []
    
    # 基础处理
    base = clean_alias(text).replace(' ', '')
    base = re.search(r'(?i)(.*?)TV', base).group(1) if 'TV' in base.upper() else base
    
    # 规则 10：语义补偿映射表
    repls = [
        (r'(?i)Satellite', ['卫视']),
        (r'(?i)News', ['新闻', '综合']),
        (r'(?i)Generalist|Comprehensive', ['综合']),
        (r'(?i)Documentary', ['纪录', '纪实']),
        (r'(?i)Childrens?|Cartoon', ['少儿', '动漫']),
        (r'(?i)Movie|Cine', ['电影']),
        (r'(?i)Sports', ['体育']),
        (r'(?i)Science', ['科教']),
        (r'(?i)BRTV', ['北京']),
        (r'(?i)SZTV', ['深圳']),
        (r'(?i)HBS', ['湖南'])
    ]
    
    variants = [base]
    for pattern, subs in repls:
        temp_list = []
        for v in variants:
            for s in subs:
                new_v = re.sub(pattern, s, v)
                if new_v != v:
                    temp_list.append(new_v)
        variants.extend(temp_list)
    
    # 转化为大写拼音 (规则 4 & 6)
    py_results = set()
    for v in set(variants):
        # 规则 4：去掉常见后缀增加碰撞
        v_pure = re.sub(r'卫视$|台$|频道$|综合$|新闻$', '', v)
        py = "".join(lazy_pinyin(v_pure)).upper()
        if py: py_results.add(py)
    return list(py_results)

def run_pipeline():
    start_time = time.time()
    headers = {'User-Agent': 'Mozilla/5.0'}
    name_map = defaultdict(set)
    search_index = {} 

    def update_index(std, val):
        """规则 5：实时更新匹配索引库"""
        if not val: return
        # A. 清洗后的全小写 (规则 6)
        search_index[clean_alias(val).lower()] = std
        # B. 拼音变体 (规则 10)
        for py in get_pinyin_variants(val):
            search_index[py] = std

    try:
        # 第一步 & 第二步：抓取标准名与别名
        print(">>> [1/4] 抓取 epg.112114.xyz 数据库...")
        r_home = requests.get("https://epg.112114.xyz", headers=headers, timeout=20, verify=False)
        # 抓取包含 title 和 页面文本的频道名
        stds = re.findall(r'title="([^"]+)"', r_home.text)
        stds += re.findall(r'[\u4e00-\u9fa5]{2,10}(?:\d+[\+]?|超高?清)?', r_home.text)
        
        for s in set(stds):
            if s and not s.startswith("http"):
                s_std = clean_alias(s) # 规则 8：粉碎 </br>
                if s_std:
                    name_map[s_std].add(s_std)
                    # 规则 9：自动加 HD 别名
                    hd_name = f"{s_std}HD"
                    name_map[s_std].add(hd_name)
                    update_index(s_std, s_std)
                    update_index(s_std, hd_name) # 让 HD 也进入索引

        # 抓取别名页
        r_alias = requests.get("https://epg.112114.xyz/alias", headers=headers, timeout=20, verify=False)
        for line in r_alias.text.splitlines():
            parts = re.split(r'-->|:', line)
            if len(parts) >= 2:
                std_raw = parts[1].strip() if '-->' in line else parts[0].strip()
                alias_raw = parts[0].strip() if '-->' in line else parts[1].strip()
                std = clean_alias(std_raw)
                if std:
                    name_map[std].add(clean_alias(alias_raw))
                    update_index(std, alias_raw)

        # 第三步：合并本地 nameoriginal.json
        if os.path.exists(ORIGINAL_JSON_PATH):
            print(f">>> [3/4] 合并本地 {os.path.basename(ORIGINAL_JSON_PATH)}...")
            with open(ORIGINAL_JSON_PATH, 'r', encoding='utf-8') as f:
                orig_data = json.load(f)
                for k, v in orig_data.items():
                    std = clean_alias(k)
                    if not std: continue
                    a_list = v.split(',') if isinstance(v, str) else v
                    for a in a_list:
                        name_map[std].add(clean_alias(a))
                        update_index(std, a)

        # 第四步：从 iptv-org 提取识别数据库
        print(">>> [4/4] 识别 iptv-org 源列表...")
        r_m3u = requests.get("https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u", headers=headers, timeout=30, verify=False)
        with open(M3U_RAW_PATH, "w", encoding="utf-8") as f: f.write(r_m3u.text)

        stats = {"total": 0, "matched": 0}
        unmatched = set()

        for line in r_m3u.text.splitlines():
            if line.startswith("#EXTINF:"):
                stats["total"] += 1
                tid = (re.search(r'tvg-id="([^"]*)"', line).group(1) if 'tvg-id="' in line else "")
                tname = (re.search(r'tvg-name="([^"]*)"', line).group(1) if 'tvg-name="' in line else "")
                dname = line.split(",")[-1].strip()

                # 规则 1：优先级判断
                best = dname if has_chinese(dname) else tid if has_chinese(tid) else tname if has_chinese(tname) else (tid or tname or dname)

                # 匹配逻辑
                target = search_index.get(clean_alias(best).lower())
                if not target:
                    for py in get_pinyin_variants(best):
                        if py in search_index:
                            target = search_index[py]
                            break

                if target:
                    # 规则 7：回填所有信息
                    for info in [tid, tname, dname]:
                        if info:
                            c = clean_alias(info)
                            if c: name_map[target].add(c)
                    update_index(target, best)
                    stats["matched"] += 1
                else:
                    unmatched.add(dname)

        # 整理并写入文件 (再次确保规则 8)
        final_json = {k: ",".join(sorted(list({clean_alias(a) for a in v if clean_alias(a)}))) 
                      for k, v in name_map.items() if k}
        
        with open(OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(final_json, f, indent=2, ensure_ascii=False)
        
        with open(UNMATCHED_PATH, 'w', encoding='utf-8') as f:
            f.write("\n".join(sorted(list(unmatched))))

        duration = time.time() - start_time
        print(f"\n==========================================")
        print(f"        name.py 任务识别报告")
        print(f"==========================================")
        print(f"- 别名识别总数: {stats['total']}")
        print(f"- 成功识别数量: {stats['matched']}")
        print(f"- 未能识别数量: {len(unmatched)}")
        print(f"- 执行总耗时: {duration:.2f} 秒")
        print(f"==========================================\n")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")

if __name__ == "__main__":
    run_pipeline()