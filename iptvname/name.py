import os
import re
import json
import time

# 尝试导入拼音库（用于规则4），如果没有安装也不报错，平滑降级使用语义库
try:
    from pypinyin import pinyin, Style
    HAS_PINYIN = True
except ImportError:
    HAS_PINYIN = False

# ================= 配置路径 =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)

# 输入文件 (按总则要求，部分在上级目录，部分在当前目录)
GROUP_JSON = os.path.join(PARENT_DIR, 'group.json')
SOURCES_CACHE = os.path.join(PARENT_DIR, 'sources_cache.txt')
NAME_ORIGINAL_JSON = os.path.join(BASE_DIR, 'nameoriginal.json')

# 输出文件 (当前目录)
NAME_JSON = os.path.join(BASE_DIR, 'name.json')
UNMATCHED_TXT = os.path.join(BASE_DIR, 'unmatched.txt')

# ================= 规则 10: 语义补偿库 =================
# 涵盖拼音翻译、常见英文后缀、错别字等，进行降维打击
SEMANTIC_LIB = {
    "SICHUANSATELLITE": "四川卫视", "ZHEJIANGSATELLITE": "浙江卫视",
    "HUNANSATELLITE": "湖南卫视", "JIANGSUSATELLITE": "江苏卫视",
    "BEIJINGSATELLITE": "北京卫视", "BTV": "北京卫视",
    "GUANGDONGSATELLITE": "广东卫视", "SHANDONGSATELLITE": "山东卫视",
    "ANHUISATELLITE": "安徽卫视", "HENANSATELLITE": "河南卫视",
    "LIAONINGSATELLITE": "辽宁卫视", "HEILONGJIANGSATELLITE": "黑龙江卫视",
    "TIANJINSATELLITE": "天津卫视", "HUBEISATELLITE": "湖北卫视",
    "SICHUAN": "四川卫视", "ZHEJIANG": "浙江卫视", "HUNAN": "湖南卫视",
    "JIANGSU": "江苏卫视", "BEIJING": "北京卫视", "SHANDONG": "山东卫视",
    "CCTV1": "CCTV-1 综合", "CCTV2": "CCTV-2 财经", "CCTV3": "CCTV-3 综艺",
    "CCTV4": "CCTV-4 中文国际", "CCTV5": "CCTV-5 体育", "CCTV5+": "CCTV-5+ 体育赛事",
    "CCTV6": "CCTV-6 电影", "CCTV7": "CCTV-7 国防军事", "CCTV8": "CCTV-8 电视剧",
    "CCTV9": "CCTV-9 纪录", "CCTV10": "CCTV-10 科教", "CCTV11": "CCTV-11 戏曲",
    "CCTV12": "CCTV-12 社会与法", "CCTV13": "CCTV-13 新闻", "CCTV14": "CCTV-14 少儿",
    "CCTV15": "CCTV-15 音乐", "CCTV16": "CCTV-16 奥林匹克", "CCTV17": "CCTV-17 农业农村",
    "CHCDONGZUO": "CHC动作电影", "CHCJIATING": "CHC家庭影院", "CHCDIANYING": "CHC高清电影"
}

# 全局数据库
name_db = {}          # 结构: { "标准名": set(["别名1", "别名2"]) }
alias_to_std = {}     # 反向索引: { "大写别名": "标准名" } 用于规则 5 快速比对
pinyin_to_std = {}    # 拼音索引: { "标准名拼音大写": "标准名" } 用于规则 4

# ================= 核心工具函数 =================

def to_pinyin_upper(text):
    """转大写拼音 (去掉空格)"""
    if not HAS_PINYIN: return text.upper()
    py_list = pinyin(text, style=Style.NORMAL)
    return "".join([item[0] for item in py_list]).replace(" ", "").upper()

def add_alias_to_db(std_name, alias):
    """规则 5 & 6: 添加别名并更新反向索引，统一大写匹配"""
    if not alias: return
    clean_alias = str(alias).strip()
    if not clean_alias: return
    
    name_db[std_name].add(clean_alias)
    alias_to_std[clean_alias.upper()] = std_name

def load_json(path):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            try: return json.load(f)
            except: return {}
    return {}

def has_chinese(text):
    """判断是否包含中文"""
    return bool(re.search(r'[\u4e00-\u9fa5]', text)) if text else False

def get_prioritized_candidates(src):
    """规则 1: 优先级 含中文优先 > tvg-id > tvg-name"""
    cands = [
        {'type': 'display', 'val': src.get('display', '')},
        {'type': 'id', 'val': src.get('id', '')},
        {'type': 'name', 'val': src.get('name', '')}
    ]
    # 过滤空值
    cands = [c for c in cands if c['val'].strip()]
    
    # 拆分为中文和非中文组
    chinese_cands = [c for c in cands if has_chinese(c['val'])]
    non_chinese_cands = [c for c in cands if not has_chinese(c['val'])]
    
    # 按 id, name (在组内) 的原始顺序排序，优先返回含中文的
    return [c['val'] for c in chinese_cands] + [c['val'] for c in non_chinese_cands]

def clean_round_1(text):
    """规则 2: 第一轮清洗，去杂质后缀和末尾空格"""
    # 激进清除要求中的字符 (忽略大小写)
    patterns = [
        r'(?i)\.cn@SD', r'(?i)\.cn@HD', r'(?i)\.hk@SD', 
        r'\(1080p\)', r'\(720p\)', r'\(576p\)', r'\(576i\)', r'\(360p\)', r'\(540p\)', r'\(480p\)', r'\(180p\)', r'\(2160p\)',
        r'(?i)\[Not 24/7\]', r'(?i)\[Geo-blocked\]', r'-'
    ]
    for p in patterns:
        text = re.sub(p, '', text)
    return text.strip()

def match_logic(target_name):
    """执行 1-4 轮的匹配逻辑，只要匹配上就返回 标准名"""
    if not target_name: return None
    
    # === 规则 2 & 5: 第一轮 ===
    clean_name = clean_round_1(target_name)
    if clean_name.upper() in alias_to_std:
        return alias_to_std[clean_name.upper()]
        
    # === 规则 3: 第二轮 (去 TV、空格，进语义库) ===
    # 比如 SichuanSatelliteTV -> SICHUANSATELLITE
    no_tv = re.sub(r'(?i)TV', '', clean_name).replace(' ', '').upper()
    if no_tv in SEMANTIC_LIB:
        translated_name = SEMANTIC_LIB[no_tv]
        if translated_name.upper() in alias_to_std:
            return alias_to_std[translated_name.upper()]
            
    # === 规则 4: 第三/四轮 (大写拼音匹配) ===
    py_target = to_pinyin_upper(no_tv)
    if py_target in pinyin_to_std:
        return pinyin_to_std[py_target]
        
    return None

# ================= 主流程 =================

def main():
    start_time = time.time()
    print("🚀 开始执行 name.py 源识别清洗任务...")

    # ===== 第一步: 读取 group.json 提取标准名 (规则 1 & 8) =====
    groups = load_json(GROUP_JSON)
    std_names_count = 0
    for group_name, channels in groups.items():
        for ch in channels:
            # 规则 8: 不能有 </br>
            std_name = ch.replace("</br>", "").strip()
            if std_name not in name_db:
                name_db[std_name] = set([std_name])
                alias_to_std[std_name.upper()] = std_name
                # 为规则4预热拼音库
                pinyin_to_std[to_pinyin_upper(std_name)] = std_name
                std_names_count += 1
    
    print(f"✅ 第一步完成: 从 group.json 提取标准名 {std_names_count} 个")

    # ===== 第二步: 合并 nameoriginal.json (规则 2) =====
    original_names = load_json(NAME_ORIGINAL_JSON)
    orig_matched = 0
    # 兼容字典格式 {"标准名": ["别名1", "别名2"]}
    if isinstance(original_names, dict):
        for key_name, aliases in original_names.items():
            if key_name in name_db:
                for alias in aliases:
                    add_alias_to_db(key_name, alias)
                orig_matched += 1
    print(f"✅ 第二步完成: 从 nameoriginal.json 合并已有别名，命中 {orig_matched} 组")

    # ===== 第三步: 解析 sources_cache.txt 识别源 =====
    sources = []
    if os.path.exists(SOURCES_CACHE):
        with open(SOURCES_CACHE, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()
            for i in range(len(lines)):
                if lines[i].startswith("#EXTINF"):
                    extinf = lines[i]
                    # 提取数据
                    tvg_id = (re.search(r'tvg-id="([^"]+)"', extinf) or [None, ""])[1]
                    tvg_name = (re.search(r'tvg-name="([^"]+)"', extinf) or [None, ""])[1]
                    display = extinf.split(',')[-1].strip() if ',' in extinf else ""
                    
                    if display or tvg_id or tvg_name:
                        sources.append({
                            'id': tvg_id,
                            'name': tvg_name,
                            'display': display,
                            'raw_line': extinf
                        })
    
    print(f"📦 解析到来源列表: {len(sources)} 条源数据，开始识别匹配...")

    # ===== 执行多轮识别匹配 =====
    matched_count = 0
    unmatched_sources = []

    for src in sources:
        candidates = get_prioritized_candidates(src)
        matched_std = None
        
        # 依次按优先级尝试匹配
        for cand in candidates:
            matched_std = match_logic(cand)
            if matched_std:
                break # 一旦某轮匹配成功，跳出尝试
        
        if matched_std:
            matched_count += 1
            # 规则 7: 匹配上了，将全部三大件均加入该标准名的别名库
            for val in [src['id'], src['name'], src['display']]:
                add_alias_to_db(matched_std, val)
        else:
            unmatched_sources.append(src)

    # ===== 规则 9: 批量注入后缀别名 =====
    for std in list(name_db.keys()):
        synthetic_aliases = [
            f"{std}HD", f"{std}.cn@SD", f"{std}.cn@HD", 
            f"{std} (2160p)", f"{std} (720p)", f"{std} (1080p)"
        ]
        for syn in synthetic_aliases:
            add_alias_to_db(std, syn)

    # ===== 收尾与导出 =====
    # 格式化导出 name.json
    final_output = {k: sorted(list(v)) for k, v in name_db.items()}
    with open(NAME_JSON, 'w', encoding='utf-8') as f:
        json.dump(final_output, f, ensure_ascii=False, indent=4)
        
    # 规则 11: 导出未匹配列表
    with open(UNMATCHED_TXT, 'w', encoding='utf-8') as f:
        f.write("=== 以下频道的 tvg-id / tvg-name / display 无法与标准名匹配 ===\n\n")
        for u in unmatched_sources:
            f.write(f"原行信息: {u['raw_line']}\n")
            f.write(f"提取字段 => id: [{u['id']}], name: [{u['name']}], display: [{u['display']}]\n")
            f.write("-" * 60 + "\n")

    # 统计数据报告
    total_aliases = sum(len(v) for v in name_db.values())
    elapsed = time.time() - start_time
    pinyin_status = "开启 (pypinyin)" if HAS_PINYIN else "未安装库, 已降级为纯语义词典映射"

    report = f"""
====================================
      📝 name.py 识别任务报告
====================================
🔹 来源总数: {len(sources)} 个源
✅ 识别成功: {matched_count} 个
❌ 未识别数: {len(unmatched_sources)} 个 (已写入 {os.path.basename(UNMATCHED_TXT)})
🗃️ 拼音匹配引擎: {pinyin_status}
🗂️ 最终别名词库: 共包含 {std_names_count} 个标准名，累计扩充至 {total_aliases} 个别名映射
⏱️ 处理总耗时: {elapsed:.2f} 秒
📁 输出文件路径: {NAME_JSON}
====================================
"""
    print(report)

if __name__ == "__main__":
    main()