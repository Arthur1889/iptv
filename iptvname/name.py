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

# 规则11：本地缓存路径定义
CACHE_EPG_HOME = os.path.join(CURRENT_DIR, "cache_epg_home.txt")
CACHE_EPG_ALIAS = os.path.join(CURRENT_DIR, "cache_epg_alias.txt")

def is_cache_valid(file_path, days=7):
    """规则11：检查缓存文件是否在 1 周内有效"""
    if not os.path.exists(file_path):
        return False
    file_time = os.path.getmtime(file_path)
    return (time.time() - file_time) < (days * 24 * 3600)

def get_content_with_cache(url, cache_path, headers, timeout=30):
    """规则11：核心缓存机制控制"""
    if is_cache_valid(cache_path, days=7):
        print(f"   [本地缓存有效] 直接读取: {os.path.basename(cache_path)}")
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()
    else:
        print(f"   [缓存失效/不存在] 正在请求网络: {url}")
        r = requests.get(url, headers=headers, timeout=timeout, verify=False)
        r.raise_for_status()
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(r.text)
        return r.text

def has_chinese(text):
    """规则1：判断含中文"""
    if not text: return False
    return any('\u4e00' <= char <= '\u9fa5' for char in text)

def clean_alias(text):
    """规则2 & 8：彻底清洗杂质、HTML标签、成对括号及末尾空格"""
    if not text: return ""
    text = str(text)
    # 规则8：粉碎所有 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    # 规则2：移除规格括号及内容
    text = re.sub(r'[\(\（\[\【\《].*?[\)\）\]\】\技巧\》]', '', text)
    # 规则2：特定后缀过滤
    junk = [
        r'\.cn@SD', r'\.cn@HD', r'\.hk@SD', r'\.png$',
        r'2160p', r'1080p', r'720p', r'576p', r'576i', r'540p', r'480p', r'360p', r'180p',
        r'\[Not 24/7\]', r'\[Geo-blocked\]'
    ]
    for p in junk:
        text = re.sub(p, '', text, flags=re.I)
    # 规则2：去横杠、去下划线并去掉末尾空格
    text = text.replace('-', '').replace('_', '').strip()
    return text

def get_pinyin_variants(text):
    """规则3, 4, 6, 10：超强矩阵式语义补偿与全大写拼音生成"""
    if not text: return []
    
    # 基础过滤：去空格、去独立TV
    base = clean_alias(text).replace(' ', '')
    base = re.sub(r'TV|Television', '', base, flags=re.I)
    
    # 规则10：矩阵式高级语义补偿库 (修复了错位括号与内嵌修饰符)
    repls = [
        (r'Satellite', ['卫视']),
        (r'News', ['新闻', '综合']),
        (r'Generalist|Comprehensive|CCTV1$', ['综合']),
        (r'Documentary|Docu|Doc', ['纪录', '纪实']),
        (r'Childrens?|Cartoon|Kaku', ['少儿', '动漫', '卡通']),
        (r'Movie|Cine|Chuanqi', ['电影', '影院', '传奇']),
        (r'Sports|Sport', ['体育']),
        (r'Science|Edu|Discovery', ['科教', '科学', '教育']),
        (r'Finance|Economy|Business', ['财经', '经济']),
        (r'Entertainment|Variety', ['综艺', '娱乐']),
        (r'International|World', ['国际']),
        (r'Music', ['音乐']),
        # 常见地方台及广播台缩写补偿
        (r'BRTV', ['北京']),
        (r'GDT?V', ['广东']),
        (r'SZTV', ['深圳']),
        (r'HBS|HUNAN', ['湖南']),
        (r'ZTV|ZJTV', ['浙江']),
        (r'SMG', ['东方', '上海'])
    ]
    
    variants = {base}
    for pattern, subs in repls:
        temp_set = set()
        for v in variants:
            for s in subs:
                new_v = re.sub(pattern, s, v, flags=re.I)
                if new_v != v:
                    temp_set.add(new_v)
        variants.update(temp_set)
    
    # 将所有生成的中文语义组合转化为大写拼音
    py_results = set()
    for v in variants:
        # 规则4：切除常见的尾部干扰后缀，扩大对撞成功率
        v_pure = re.sub(r'卫视$|台$|频道$|综合$|新闻$|卡通$|少儿$', '', v)
        py = "".join(lazy_pinyin(v_pure)).upper()
        if py: py_results.add(py)
        
    return list(py_results)

def run_pipeline():
    start_time = time.time()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    name_map = defaultdict(set)
    search_index = {} # 全局滚动查找匹配索引

    def update_index(std, val):
        """规则 5：实时更新匹配索引库"""
        if not val: return
        search_index[clean_alias(val).lower()] = std
        for py in get_pinyin_variants(val):
            search_index[py] = std

    try:
        # --- 第一步：抓取/读取标准名库 ---
        print(">>> [1/4] 获取 epg.112114.xyz 标准主页数据...")
        html_home = get_content_with_cache(
            url="https://epg.112114.xyz", 
            cache_path=CACHE_EPG_HOME, 
            headers=headers, 
            timeout=25
        )
        stds = re.findall(r'title="([^"]+)"', html_home)
        stds += re.findall(r'[\u4e00-\u9fa5]{2,10}(?:\d+[\+]?|超高?清)?', html_home)
        
        for s in set(stds):
            if s and not s.startswith("http"):
                s_std = clean_alias(s) # 规则8
                if s_std:
                    name_map[s_std].add(s_std)
                    # 规则9：每个标准名自动额外追加一个 "标准名HD" 别名
                    hd_name = f"{s_std}HD"
                    name_map[s_std].add(hd_name)
                    update_index(s_std, s_std)
                    update_index(s_std, hd_name)

        # --- 第二步：提取线上别名映射表 ---
        print(">>> [2/4] 获取 epg.112114.xyz/alias 别名数据...")
        html_alias = get_content_with_cache(
            url="https://epg.112114.xyz/alias", 
            cache_path=CACHE_EPG_ALIAS, 
            headers=headers, 
            timeout=25
        )
        for line in html_alias.splitlines():
            parts = re.split(r'-->|:', line)
            if len(parts) >= 2:
                std_raw = parts[1].strip() if '-->' in line else parts[0].strip()
                alias_raw = parts[0].strip() if '-->' in line else parts[1].strip()
                std = clean_alias(std_raw)
                if std:
                    name_map[std].add(clean_alias(alias_raw))
                    update_index(std, alias_raw)

        # --- 第三步：识别并读入本地 nameoriginal.json ---
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

        # --- 第四步：抓取/读取需识别数据库 (iptv-org M3U) ---
        print(">>> [4/4] 获取并处理 iptv-org 源列表...")
        m3u_text = get_content_with_cache(
            url="https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u",
            cache_path=M3U_RAW_PATH, 
            headers=headers,
            timeout=35
        )

        stats = {"total": 0, "matched": 0}
        unmatched = set()

        for line in m3u_text.splitlines():
            if line.startswith("#EXTINF:"):
                stats["total"] += 1
                tid = (re.search(r'tvg-id="([^"]*)"', line).group(1) if 'tvg-id="' in line else "")
                tname = (re.search(r'tvg-name="([^"]*)"', line).group(1) if 'tvg-name="' in line else "")
                dname = line.split(",")[-1].strip()

                best = dname if has_chinese(dname) else tid if has_chinese(tid) else tname if has_chinese(tname) else (tid or tname or dname)

                target = search_index.get(clean_alias(best).lower()) 
                if not target:
                    for py in get_pinyin_variants(best):
                        if py in search_index:
                            target = search_index[py]
                            break

                if target:
                    for info in [tid, tname, dname]:
                        if info:
                            c = clean_alias(info)
                            if c: name_map[target].add(c)
                    update_index(target, best)
                    stats["matched"] += 1
                else:
                    unmatched.add(dname)

        # 最终整理输出 (确保规则8)
        final_json = {}
        for k, v in name_map.items():
            k_clean = clean_alias(k)
            if k_clean:
                v_clean = {clean_alias(a) for a in v if clean_alias(a)}
                final_json[k_clean] = ",".join(sorted(list(v_clean)))
        
        with open(OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(final_json, f, indent=2, ensure_ascii=False)
        
        with open(UNMATCHED_PATH, 'w', encoding='utf-8') as f:
            f.write("\n".join(sorted(list(unmatched))))

        duration = time.time() - start_time
        print(f"\n==========================================")
        print(f"        name.py 任务识别报告")
        print(f"==========================================")
        print(f"- 别名识别总数: {stats['total']}")
        print(f"- 成功匹配数量: {stats['matched']}")
        print(f"- 未能识别数量: {len(unmatched)}")
        print(f"- 执行总耗时: {duration:.2f} 秒")
        print(f"==========================================\n")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")

if __name__ == "__main__":
    run_pipeline()