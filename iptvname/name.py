import os
import re
import json
import time

# 尝试导入拼音库，未安装则降级为纯语义匹配 (不会报错卡壳)
try:
    from pypinyin import pinyin, Style
    HAS_PINYIN = True
except ImportError:
    HAS_PINYIN = False

# ================= 路径配置 =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)

# 输入文件
GROUP_JSON = os.path.join(PARENT_DIR, 'group.json')
SOURCES_CACHE = os.path.join(PARENT_DIR, 'sources_cache.txt')

# 兼容识别你的 nameoriginal 文件格式 (.txt 或 .json)
NAME_ORIGINAL_TXT = os.path.join(BASE_DIR, 'nameoriginal.txt')
NAME_ORIGINAL_JSON = os.path.join(BASE_DIR, 'nameoriginal.json')

# 输出文件
NAME_JSON = os.path.join(BASE_DIR, 'name.json')
UNMATCHED_TXT = os.path.join(BASE_DIR, 'unmatched.txt')

# ================= 规则 10: 超级语义补偿库 =================
# 一个词对应多个可能的语义，全方位拦截降维打击
SEMANTIC_LIB = {
    "SICHUANSATELLITE": ["四川卫视", "四川台"],
    "ZHEJIANGSATELLITE": ["浙江卫视", "浙江台"],
    "HUNANSATELLITE": ["湖南卫视", "湖南台"],
    "JIANGSUSATELLITE": ["江苏卫视", "江苏台"],
    "BEIJINGSATELLITE": ["北京卫视", "北京台"],
    "BTV": ["北京卫视"],
    "GUANGDONGSATELLITE": ["广东卫视", "广东台"],
    "SHANDONGSATELLITE": ["山东卫视", "山东台"],
    "ANHUISATELLITE": ["安徽卫视", "安徽台"],
    "HENANSATELLITE": ["河南卫视", "河南台"],
    "LIAONINGSATELLITE": ["辽宁卫视", "辽宁台"],
    "HEILONGJIANGSATELLITE": ["黑龙江卫视", "龙江卫视"],
    "TIANJINSATELLITE": ["天津卫视", "天津台"],
    "HUBEISATELLITE": ["湖北卫视", "湖北台"],
    "SICHUAN": ["四川卫视"], "ZHEJIANG": ["浙江卫视"],
    "HUNAN": ["湖南卫视"], "JIANGSU": ["江苏卫视"],
    "BEIJING": ["北京卫视"], "SHANDONG": ["山东卫视"],
    "CCTV1": ["CCTV1", "CCTV-1", "CCTV-1综合", "中央一台", "中央一套"],
    "CCTV2": ["CCTV2", "CCTV-2", "CCTV-2财经", "中央二台", "中央二套"],
    "CCTV3": ["CCTV3", "CCTV-3", "CCTV-3综艺", "中央三台", "中央三套"],
    "CCTV4": ["CCTV4", "CCTV-4", "CCTV-4中文国际", "中央四台", "中央四套"],
    "CCTV5": ["CCTV5", "CCTV-5", "CCTV-5体育", "中央五台", "中央五套"],
    "CCTV5+": ["CCTV5+", "CCTV-5+", "CCTV-5+体育赛事"],
    "CCTV6": ["CCTV6", "CCTV-6", "CCTV-6电影", "中央六台", "中央六套"],
    "CCTV7": ["CCTV7", "CCTV-7", "CCTV-7国防军事", "中央七台", "中央七套"],
    "CCTV8": ["CCTV8", "CCTV-8", "CCTV-8电视剧", "中央八台", "中央八套"],
    "CCTV9": ["CCTV9", "CCTV-9", "CCTV-9纪录", "中央九台", "中央九套"],
    "CCTV10": ["CCTV10", "CCTV-10", "CCTV-10科教", "中央十台", "中央十套"],
    "CCTV11": ["CCTV11", "CCTV-11", "CCTV-11戏曲", "中央十一台", "中央十一套"],
    "CCTV12": ["CCTV12", "CCTV-12", "CCTV-12社会与法", "中央十二台"],
    "CCTV13": ["CCTV13", "CCTV-13", "CCTV-13新闻", "中央十三台", "中央十三套"],
    "CCTV14": ["CCTV14", "CCTV-14", "CCTV-14少儿", "中央十四台", "中央十四套"],
    "CCTV15": ["CCTV15", "CCTV-15", "CCTV-15音乐", "中央十五台", "中央十五套"],
    "CCTV16": ["CCTV16", "CCTV-16", "CCTV-16奥林匹克", "中央十六台"],
    "CCTV17": ["CCTV17", "CCTV-17", "CCTV-17农业农村", "中央十七台"],
    "CCTV4K": ["CCTV4K", "CCTV-4K", "CCTV-4K超高清"],
    "CCTV8K": ["CCTV8K", "CCTV-8K", "CCTV-8K超高清"],
    "CGTN": ["CGTN", "中国国际电视台"],
    "PHOENIXINFO": ["凤凰卫视资讯台", "凤凰资讯"],
    "PHOENIXCHINESE": ["凤凰卫视中文台", "凤凰中文"],
    "PHOENIXHONGKONG": ["凤凰卫视香港台", "凤凰香港"],
    "CHCDONGZUO": ["CHC动作电影"], "CHCJIATING": ["CHC家庭影院"], "CHCDIANYING": ["CHC高清电影"]
}

# 全局数据库
name_db = {}          # { "标准名": set(["别名1", "别名2"]) }
alias_to_std = {}     # { "大写别名": "标准名" }
pinyin_to_std = {}    # { "大写拼音": "标准名" }

# ================= 核心工具函数 =================

def to_pinyin_upper(text):
    """转大写拼音 (无空格)"""
    if not HAS_PINYIN: return text.upper()
    py_list = pinyin(text, style=Style.NORMAL)
    return "".join([item[0] for item in py_list]).replace(" ", "").upper()

def add_std(std_name):
    """添加标准名 (包含过滤规则8: 不能有 </br>)"""
    std_name = str(std_name).replace("</br>", "").strip()
    if not std_name: return
    if std_name not in name_db:
        name_db[std_name] = set()
        add_alias(std_name, std_name) # 标准名自身也是一种别名
        pinyin_to_std[to_pinyin_upper(std_name)] = std_name

def add_alias(std_name, alias):
    """添加别名，自动更新匹配索引 (支持规则5: 滚雪球式更新匹配库)"""
    alias = str(alias).strip()
    if not alias: return
    name_db[std_name].add(alias)
    # 规则 6: 统一转大写建立索引
    alias_to_std[alias.upper()] = std_name

def load_json(path):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            try: return json.load(f)
            except: return {}
    return {}

def has_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fa5]', text)) if text else False

def get_candidates(src):
    """规则 1: 提取比对字段，严格遵循 优先级：含中文 > tvg-id > tvg-name"""
    id_val = src.get('id', '').strip()
    name_val = src.get('name', '').strip()
    disp_val = src.get('display', '').strip()
    
    chinese_cands = []
    other_cands = []
    
    # 优先挑出所有含中文的
    for v in [disp_val, id_val, name_val]:
        if v and has_chinese(v) and v not in chinese_cands:
            chinese_cands.append(v)
            
    # 其余遵循规则: tvg-id > tvg-name > display
    for v in [id_val, name_val, disp_val]:
        if v and not has_chinese(v) and v not in other_cands:
            other_cands.append(v)
            
    return chinese_cands + other_cands

def clean_round_1(text):
    """规则 2: 第一轮去杂质后缀"""
    patterns = [
        r'(?i)\.cn@sd', r'(?i)\.cn@hd', r'(?i)\.hk@sd', 
        r'\(\s*1080[pi]\s*\)', r'\(\s*720[pi]\s*\)', r'\(\s*576[pi]\s*\)', r'\(\s*360[pi]\s*\)',
        r'\(\s*540[pi]\s*\)', r'\(\s*480[pi]\s*\)', r'\(\s*180[pi]\s*\)', r'\(\s*2160[pi]\s*\)',
        r'(?i)\[not 24/7\]', r'(?i)\[geo-blocked\]', r'-'
    ]
    res = text
    for p in patterns:
        res = re.sub(p, '', res)
    return res.rstrip() # 去掉末尾空格

def clean_round_2(text):
    """规则 3: 去除 TV、空格，准备进行中文/拼音映射"""
    res = re.sub(r'(?i)TV', '', text)
    res = res.replace(' ', '')
    return res

# ================= 主流程 =================

def main():
    start_time = time.time()
    print("🚀 开始执行严谨版 name.py 识别清洗任务...")

    # ===== 第一步: 从 group.json 提取标准名 =====
    groups = load_json(GROUP_JSON)
    for category, channel_str in groups.items():
        # 🚨 破案了：从字符串里劈出频道名，而不是劈字母
        if isinstance(channel_str, str):
            channels = [x.strip() for x in channel_str.split(',') if x.strip()]
            for ch in channels:
                add_std(ch)

    print(f"✅ 第一步: 导入 group.json 完成。当前标准名池: {len(name_db)} 个")

    # ===== 第二步: 合并 nameoriginal 文件 =====
    # 智能兼容加载逻辑 (完美支持你的 nameoriginal.txt 或 json)
    orig_data = []
    if os.path.exists(NAME_ORIGINAL_JSON):
        try:
            raw = load_json(NAME_ORIGINAL_JSON)
            if isinstance(raw, list): orig_data = raw
            elif isinstance(raw, dict): orig_data = [[k] + (v if isinstance(v, list) else [v]) for k, v in raw.items()]
        except: pass
    elif os.path.exists(NAME_ORIGINAL_TXT):
        with open(NAME_ORIGINAL_TXT, 'r', encoding='utf-8') as f:
            for line in f:
                parts = [x.strip() for x in line.split(',') if x.strip()]
                if parts: orig_data.append(parts)

    for group in orig_data:
        std = group[0]
        # 💡 "如果出现了group.json中没有的标准名，以nameoriginal中的为准"
        add_std(std) 
        for alias in group[1:]:
            add_alias(std, alias)

    print(f"✅ 第二步: 合并原始别名库完成。当前标准名池扩展至: {len(name_db)} 个")

    # ===== 插入步骤 (规则 9): 提前批量生成后缀别名 =====
    # 💡 为什么要提前？因为提前把 "CCTV1HD" 放入别名库，在第三步遇到 M3U 里的 "CCTV1HD" 就能瞬间秒配！
    for std in list(name_db.keys()):
        suffixes = ["HD", ".cn@SD", ".cn@HD", " (2160p)", " (720p)", " (1080p)"]
        for suf in suffixes:
            add_alias(std, f"{std}{suf}")

    # ===== 第三步: 解析 sources_cache.txt =====
    sources = []
    if os.path.exists(SOURCES_CACHE):
        with open(SOURCES_CACHE, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()
            for i in range(len(lines)):
                if lines[i].startswith("#EXTINF"):
                    line = lines[i]
                    tvg_id = (re.search(r'tvg-id="([^"]*)"', line) or [None, ""])[1]
                    tvg_name = (re.search(r'tvg-name="([^"]*)"', line) or [None, ""])[1]
                    display = line.split(',')[-1].strip() if ',' in line else ""
                    sources.append({'id': tvg_id, 'name': tvg_name, 'display': display, 'raw_line': line})

    print(f"📦 解析到 {len(sources)} 条源数据，开始深度匹配...")

    # ===== 执行多级匹配逻辑 =====
    matched_count = 0
    unmatched_sources = []

    for src in sources:
        candidates = get_candidates(src)
        matched_std = None

        for cand in candidates:
            # 第一轮：直接去杂质比对
            clean1 = clean_round_1(cand)
            if clean1.upper() in alias_to_std:
                matched_std = alias_to_std[clean1.upper()]
                break
                
            # 第二轮：去 TV 查纯净版
            clean2 = clean_round_2(clean1)
            if clean2.upper() in alias_to_std:
                matched_std = alias_to_std[clean2.upper()]
                break
                
            # 规则 10: 尝试语义库补偿匹配
            if clean2.upper() in SEMANTIC_LIB:
                for s_cand in SEMANTIC_LIB[clean2.upper()]:
                    if s_cand.upper() in alias_to_std:
                        matched_std = alias_to_std[s_cand.upper()]
                        break
            if matched_std: break

            # 第三/四轮：强转大写拼音匹配
            py_cand = to_pinyin_upper(clean2)
            if py_cand in pinyin_to_std:
                matched_std = pinyin_to_std[py_cand]
                break

        # 规则 7: 匹配成功，将源的三要素全部塞进别名库，滚雪球式壮大图谱
        if matched_std:
            matched_count += 1
            for val in [src['id'], src['name'], src['display']]:
                if val: add_alias(matched_std, val)
        else:
            unmatched_sources.append(src)

    # ===== 终末导出 =====
    # 输出整洁排版的 JSON
    final_output = {k: sorted(list(v)) for k, v in name_db.items()}
    with open(NAME_JSON, 'w', encoding='utf-8') as f:
        json.dump(final_output, f, ensure_ascii=False, indent=4)
        
    # 规则 11: 未匹配清单
    with open(UNMATCHED_TXT, 'w', encoding='utf-8') as f:
        f.write("=== 以下频道的源无法与任何标准名匹配 ===\n\n")
        for u in unmatched_sources:
            f.write(f"源行: {u['raw_line']}\n")
            f.write(f"提取 => id: [{u['id']}], name: [{u['name']}], display: [{u['display']}]\n")
            f.write("-" * 60 + "\n")

    # 统计数据
    total_aliases = sum(len(v) for v in name_db.values())
    elapsed = time.time() - start_time
    pinyin_status = "🟢 开启 (pypinyin原生驱动)" if HAS_PINYIN else "🟡 降级 (内置语义字典驱动)"
    
    print(f"""
====================================
      📝 name.py 深度识别任务报告
====================================
🔹 M3U 缓存来源: {len(sources)} 条源
✅ 识别匹配成功: {matched_count} 条
❌ 未识别被遗弃: {len(unmatched_sources)} 条 (详见 unmatched.txt)
🗃️ 拼音匹配引擎: {pinyin_status}
🗂️ 最终别名图谱: 共 {len(name_db)} 个标准名，扩容至 {total_aliases} 个极品别名
⏱️ 脚本处理耗时: {elapsed:.2f} 秒
📁 终极名册输出: {os.path.basename(NAME_JSON)}
====================================
""")

if __name__ == "__main__":
    main()