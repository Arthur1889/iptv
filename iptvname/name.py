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
ORIGINAL_TXT_PATH = os.path.join(CURRENT_DIR, "nameoriginal.txt") 
OUTPUT_JSON_PATH = os.path.join(CURRENT_DIR, "name.json")
UNMATCHED_PATH = os.path.join(CURRENT_DIR, "unmatched.txt")

def has_chinese(text):
    """规则1：判断是否含中文"""
    if not text: return False
    return any('\u4e00' <= char <= '\u9fa5' for char in text)

def clean_alias(text):
    """规则2：去掉杂质字符及规格括号，最后严格去掉末尾空格"""
    if not text: return ""
    # 2.1 移除所有规格括号及其内容: (1080p), [Not 24/7] 等
    # 修正点：精准匹配括号对，不包含内部多余空格，防止残留
    text = re.sub(r'[\(\（\[\【\《].*?[\)\）\]\】\》]', '', text)
    
    # 2.2 去掉规则2指定的特定后缀
    junk = [
        r'\.cn@SD', r'\.cn@HD', r'\.hk@SD', r'\.png$',
        r'2160p', r'1080p', r'720p', r'576p', r'576i', r'540p', r'480p', r'360p', r'180p'
    ]
    for p in junk:
        text = re.sub(p, '', text, flags=re.I)
    
    # 2.3 去掉横杠并严格落实规则：去掉末尾空格
    return text.replace('-', '').replace('_', '').strip()

def to_upper_pinyin(text):
    """规则3&4&6：语义转换、去TV、去空格、转大写拼音"""
    if not text: return ""
    # 规则3：语义翻译 (如 Satellite -> 卫视)
    text = re.sub(r'(?i)Satellite', '卫视', text)
    text = re.sub(r'(?i)Generalist|Comprehensive|News', '综合', text)
    text = re.sub(r'(?i)TV|Television', '', text) 
    
    # 清洗并去掉所有空格
    pure = clean_alias(text).replace(' ', '')
    # 规则4：去掉后缀以增强碰撞成功率
    pure = re.sub(r'卫视$|台$|频道$|综合$', '', pure)
    # 规则6：统一大小写
    return "".join(lazy_pinyin(pure)).upper()

def run_pipeline():
    start_time = time.time()
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    name_map = defaultdict(set)
    search_index = {} 
    stats = {"m3u_total": 0, "m3u_matched": 0, "local_lines": 0}

    def update_index(std, val):
        """规则5：滚动更新索引库"""
        if not val: return
        search_index[clean_alias(val).lower()] = std
        search_index[to_upper_pinyin(val)] = std

    try:
        # --- 第一步：抓取标准名 (112114.xyz) ---
        print(">>> [1/4] 抓取标准名库...")
        r_home = requests.get("https://epg.112114.xyz", headers=headers, timeout=20, verify=False)
        stds = re.findall(r'title="([^"]+)"', r_home.text)
        stds += re.findall(r'[\u4e00-\u9fa5]{2,10}(?:\d+[\+]?|超高?清)?', r_home.text)
        for s in set(stds):
            if s and not s.startswith("http"):
                s_std = s.strip()
                name_map[s_std].add(s_std)
                update_index(s_std, s_std)

        # --- 第二步：提取别名映射 (alias) ---
        print(">>> [2/4] 抓取线上别名映射...")
        r_alias = requests.get("https://epg.112114.xyz/alias", headers=headers, timeout=20, verify=False)
        for line in r_alias.text.splitlines():
            parts = re.split(r'-->|:', line)
            if len(parts) >= 2:
                std, alias = (parts[1].strip(), parts[0].strip()) if '-->' in line else (parts[0].strip(), parts[1].strip())
                name_map[std].add(alias)
                update_index(std, alias)

        # --- 第三步：识别本地 nameoriginal.txt ---
        if os.path.exists(ORIGINAL_TXT_PATH):
            print(f">>> [3/4] 正在解析本地 {os.path.basename(ORIGINAL_TXT_PATH)}...")
            with open(ORIGINAL_TXT_PATH, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith("#") or not line.strip(): continue
                    parts = line.strip().split(',')
                    if parts:
                        std = parts[0].strip()
                        stats["local_lines"] += 1
                        for p in parts:
                            p_val = p.strip()
                            if p_val:
                                name_map[std].add(p_val)
                                update_index(std, p_val)

        # --- 第四步：打捞识别 iptv-org ---
        print(">>> [4/4] 正在识别 [iptv-org](https://github.com/iptv-org/iptv/blob/master/streams/cn.m3u) 源并落实规则 7...")
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

                # 规则1：优先级判定
                best_raw = ""
                if has_chinese(dname): best_raw = dname
                elif has_chinese(tid): best_raw = tid
                elif has_chinese(tname): best_raw = tname
                else: best_raw = tid if tid else tname if tname else dname

                # 多轮匹配 (包含规则 6 大小写统一)
                target_std = search_index.get(clean_alias(best_raw).lower())
                if not target_std:
                    target_std = search_index.get(to_upper_pinyin(best_raw))

                if target_std:
                    # 规则 7：匹配成功，回填 tvg-id, tvg-name, 显示姓名
                    for info in [tid, tname, dname]:
                        if info: name_map[target_std].add(info.strip())
                    
                    # 规则 5：滚动更新索引
                    update_index(target_std, dname)
                    stats["m3u_matched"] += 1
                else:
                    unmatched.add(dname)

        # 保存为 name.json
        final_json = {k: ",".join(sorted(list(v))) for k, v in name_map.items() if k}
        with open(OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(final_json, f, indent=2, ensure_ascii=False)
        
        with open(UNMATCHED_PATH, 'w', encoding='utf-8') as f:
            f.write("\n".join(sorted(list(unmatched))))

        # 任务报告
        duration = time.time() - start_time
        print("\n" + "="*50)
        print("              name.py 任务识别报告")
        print("="*50)
        print(f"- 本地文件加载: {stats['local_lines']} 行")
        print(f"- 源扫描总数 (iptv-org): {stats['m3u_total']} 个")
        print(f"- 成功识别匹配: {stats['m3u_matched']} 个")
        print(f"- 未能识别数量: {len(unmatched)} 个 (见 unmatched.txt)")
        print(f"- 最终标准频道总数: {len(final_json)} 个")
        print(f"- 运行耗时: {duration:.2f} 秒")
        print("="*50 + "\n")

    except Exception as e:
        print(f"\n❌ 执行出错: {e}")

if __name__ == "__main__":
    run_pipeline()