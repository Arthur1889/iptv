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
ORIGINAL_TXT_PATH = os.path.join(CURRENT_DIR, "nameoriginal.txt") 
OUTPUT_JSON_PATH = os.path.join(CURRENT_DIR, "name.json")
UNMATCHED_PATH = os.path.join(CURRENT_DIR, "unmatched.txt")

def has_chinese(text):
    """规则1：判断是否含中文"""
    return any('\u4e00' <= char <= '\u9fa5' for char in text) if text else False

def clean_alias(text):
    """规则2：去掉杂质字符"""
    if not text: return ""
    # 移除所有类型的括号内容 (规则2: 括号)
    text = re.sub(r'[\(\（\[\【\《].*?[\)\）\]\】\ \》]', '', text)
    # 规则2: 去掉指定后缀及字符
    junk = [
        r'\.cn@SD', r'\.cn@HD', r'\.hk@SD', r'\.png$',
        r'2160p', r'1080p', r'720p', r'576p', r'576i', r'540p', r'480p', r'360p', r'180p',
        r'\[Not 24/7\]', r'\[Geo-blocked\]', r'\s+'
    ]
    for p in junk:
        text = re.sub(p, '', text, flags=re.I)
    return text.replace('-', '').replace('_', '').strip()

def to_upper_pinyin(text):
    """规则3&4&6：语义翻译、大写拼音、统一大小写"""
    if not text: return ""
    # 规则3：语义翻译 (如 Satellite -> 卫视)
    text = re.sub(r'(?i)Satellite', '卫视', text)
    text = re.sub(r'(?i)Generalist|Comprehensive|News', '综合', text)
    text = re.sub(r'(?i)TV|Television', '', text) # 规则3: 去掉TV
    
    pure = clean_alias(text)
    # 规则4：归一化处理 (大写拼音碰撞)
    pure = re.sub(r'卫视$|台$|频道$|综合$', '', pure)
    # 规则6: 统一大小写 (拼音已包含此逻辑)
    return "".join(lazy_pinyin(pure)).upper()

def run_pipeline():
    start_time = time.time()
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    # 核心库：{ 标准名: {别名1, 别名2} }
    name_map = defaultdict(set)
    # 搜索索引：{ 清洗名/大写拼音: 标准名 }
    search_index = {}
    
    # 统计数据
    stats = {"local_lines": 0, "m3u_total": 0, "m3u_matched": 0}

    def update_index(std, val):
        """规则5：滚动更新索引"""
        if not val: return
        # 存入清洗名索引
        search_index[clean_alias(val).lower()] = std
        # 存入大写拼音索引 (规则4)
        search_index[to_upper_pinyin(val)] = std

    try:
        # --- 第一步：抓取标准名 (epg.112114.xyz) ---
        print(">>> [1/4] 正在抓取线上 [标准名库](https://epg.112114.xyz)...")
        r_home = requests.get("https://epg.112114.xyz", headers=headers, timeout=20, verify=False)
        # 提取网页中的标准名
        stds = re.findall(r'title="([^"]+)"', r_home.text)
        stds += re.findall(r'[\u4e00-\u9fa5]{2,10}(?:\d+[\+]?|超高?清)?', r_home.text)
        for s in set(stds):
            name_map[s].add(s)
            update_index(s, s)

        # --- 第二步：提取 Alias 别名库 (epg.112114.xyz/alias) ---
        print(">>> [2/4] 正在抓取线上 [别名映射](https://epg.112114.xyz/alias)...")
        r_alias = requests.get("https://epg.112114.xyz/alias", headers=headers, timeout=20, verify=False)
        for line in r_alias.text.splitlines():
            # 兼容 112114 的两种格式 (A --> B 别名在前；A:B 标准在前)
            parts = re.split(r'-->|:', line)
            if len(parts) >= 2:
                std, alias = (parts[1].strip(), parts[0].strip()) if '-->' in line else (parts[0].strip(), parts[1].strip())
                if std and alias:
                    name_map[std].add(alias)
                    update_index(std, alias)

        # --- 第三步：解析本地 nameoriginal.txt ---
        if os.path.exists(ORIGINAL_TXT_PATH):
            print(f">>> [3/4] 正在处理本地 {os.path.basename(ORIGINAL_TXT_PATH)}...")
            with open(ORIGINAL_TXT_PATH, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith("#") or not line.strip(): continue
                    parts = line.strip().split(',')
                    if parts:
                        std = parts[0].strip() # 规则：第一列是标准名
                        stats["local_lines"] += 1
                        for p in parts:
                            p_val = p.strip()
                            if p_val:
                                name_map[std].add(p_val)
                                update_index(std, p_val)

        # --- 第四步：抓取并识别 iptv-org M3U ---
        print(">>> [4/4] 正在抓取并识别 [iptv-org](https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u) 源列表...")
        r_m3u = requests.get("https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u", headers=headers, timeout=30, verify=False)
        with open(M3U_RAW_PATH, "w", encoding="utf-8") as f:
            f.write(r_m3u.text)

        unmatched = set()
        for line in r_m3u.text.splitlines():
            if line.startswith("#EXTINF:"):
                stats["m3u_total"] += 1
                # 安全提取，修复 NoneType 报错
                tid_m = re.search(r'tvg-id="([^"]*)"', line)
                tname_m = re.search(r'tvg-name="([^"]*)"', line)
                tid, tname = (tid_m.group(1) if tid_m else ""), (tname_m.group(1) if tname_m else "")
                dname = line.split(",")[-1].strip()

                # 规则1：优先级判定 (中文 > tvg-id > tvg-name)
                candidates = [dname, tid, tname]
                best_raw = dname
                for c in candidates:
                    if has_chinese(c):
                        best_raw = c
                        break
                else:
                    best_raw = tid if tid else tname

                # 执行匹配 (规则5：滚动查找索引)
                target_std = search_index.get(clean_alias(best_raw).lower())
                if not target_std:
                    target_std = search_index.get(to_upper_pinyin(best_raw))

                if target_std:
                    name_map[target_std].add(dname)
                    update_index(target_std, dname) # 规则5: 匹配成功即写入索引
                    stats["m3u_matched"] += 1
                else:
                    unmatched.add(dname)

        # --- 最终输出 ---
        final_data = {k: ",".join(sorted(list(v))) for k, v in name_map.items() if k}
        with open(OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, indent=2, ensure_ascii=False)
        
        with open(UNMATCHED_PATH, 'w', encoding='utf-8') as f:
            f.write("\n".join(sorted(list(unmatched))))

        # 任务报告
        duration = time.time() - start_time
        print(f"""
==================================================
              name.py 任务识别报告
==================================================
1. 本地库 [nameoriginal.txt]
   - 加载标准名行数: {stats['local_lines']} 行

2. 线上库 [iptv-org]
   - 抓取别名总数: {stats['m3u_total']} 个
   - 成功识别匹配: {stats['m3u_matched']} 个
   - 未能识别数量: {len(unmatched)} 个 (见 unmatched.txt)

3. 总体结果
   - 识别标准频道总数: {len(final_data)} 个
   - 总计运行耗时: {duration:.2f} 秒
==================================================
        """)

    except Exception as e:
        print(f"\n❌ 出错: {e}")

if __name__ == "__main__":
    run_pipeline()