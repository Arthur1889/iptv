import json
import re
import os
import time

# ================= 配置路径 =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)
NAME_JSON = os.path.join(BASE_DIR, 'name.json')
NAME_ORIGINAL_JSON = os.path.join(BASE_DIR, 'nameoriginal.json')
GROUP_JSON = os.path.join(PARENT_DIR, 'group.json')
SOURCES_CACHE = os.path.join(PARENT_DIR, 'sources_cache.txt')

# ================= 规则10：深度语义补偿库 =================
SEMANTIC_DB = {
    "SICHUANSATELLITE": "四川卫视",
    "SICHUAN": "四川卫视",
    "HUNANSATELLITE": "湖南卫视",
    "HUNAN": "湖南卫视",
    "ZHEJIANG": "浙江卫视",
    "JIANGSU": "江苏卫视",
    "BEIJING": "北京卫视",
    "DONGFANG": "东方卫视",
    "DRAGON": "东方卫视",  
    "ANHUI": "安徽卫视",
    "GUANGDONG": "广东卫视",
    "SHENZHEN": "深圳卫视",
    "SHANDONG": "山东卫视",
    "SIPING": "四平新闻综合",
    "ANDO": "安多卫视",
    "XINWENZONGHE": "新闻综合",
    "CCTV1": "CCTV1",
    "CGTN": "CGTN"
}

def clean_rule2(s):
    """规则2：去除干扰字符及末尾空格"""
    patterns = [
        r'\.cn@SD', r'\.cn@HD', r'\.hk@SD', 
        r'\(1080p\)', r'\(720p\)', r'\(576p\)', r'\(576i\)', r'\(360p\)', 
        r'\(540p\)', r'\(480p\)', r'\(180p\)', r'\(2160p\)',
        r'\[Not 24/7\]', r'\[Geo-blocked\]', r'-',
        r'HD', r'SD', r'FHD', r'4K', r'8K' # 增强：将分辨率标识剔除，保证基础名纯净
    ]
    for p in patterns:
        s = re.sub(p, '', s, flags=re.IGNORECASE)
    return s.rstrip()  

def apply_rule3(s):
    """规则3：去除TV、空格，并调用语义库转化为中文"""
    s = re.sub(r'TV', '', s, flags=re.IGNORECASE).replace(' ', '')
    return SEMANTIC_DB.get(s.upper(), s)

def load_json(path):
    if not os.path.exists(path): return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def parse_m3u_line(line):
    """从源文件中安全提取 M3U 标签属性"""
    tvg_id = re.search(r'tvg-id="([^"]+)"', line)
    tvg_id = tvg_id.group(1) if tvg_id else ""
    
    tvg_name = re.search(r'tvg-name="([^"]+)"', line)
    tvg_name = tvg_name.group(1) if tvg_name else ""
    
    display_name = line.split(',')[-1].strip() if ',' in line else ""
    return tvg_id, tvg_name, display_name

def run_name_processor():
    start_time = time.time()
    
    # === 第一步：从 group.json 提取标准名 (关键修复) ===
    groups = load_json(GROUP_JSON)
    name_db = {}
    
    for group_title, channels in groups.items():
        # group.json 的 value 可能是逗号分隔的字符串，也可能是数组
        if isinstance(channels, str):
            channel_list = [c.strip() for c in channels.split(',')]
        elif isinstance(channels, list):
            channel_list = [str(c).strip() for c in channels]
        else:
            continue
            
        for std in channel_list:
            if not std: continue
            std_name = std.replace("</br>", "") # 规则8
            if std_name not in name_db:
                name_db[std_name] = [std_name]
        
    # === 第二步：合并 nameoriginal.json ===
    original = load_json(NAME_ORIGINAL_JSON)
    for k, v in original.items():
        std_name = k.replace("</br>", "")
        if std_name not in name_db:
            name_db[std_name] = [std_name]
        name_db[std_name].extend(v if isinstance(v, list) else [v])

    # === 第三步：提取 sources_cache.txt ===
    sources = []
    if os.path.exists(SOURCES_CACHE):
        with open(SOURCES_CACHE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#EXTINF'):
                    sources.append(parse_m3u_line(line))

    matched_count = 0
    total_count = len(sources)

    # === 核心识别引擎 ===
    for tvg_id, tvg_name, display_name in sources:
        if not display_name and not tvg_id and not tvg_name: 
            continue

        # 规则1：判断含中文优先级
        candidates = [display_name, tvg_id, tvg_name]
        target_name = ""
        for name in candidates:
            if re.search(r'[\u4e00-\u9fa5]', name):
                target_name = name
                break
        if not target_name:
            target_name = tvg_id if tvg_id else (tvg_name if tvg_name else display_name)
            
        if not target_name: continue

        # 规则2 & 3：清洗准备
        r1_name = clean_rule2(target_name)
        r2_name = apply_rule3(r1_name)

        matched_std = None

        # 规则5：拿已经写入 name_db 的别名实时比较
        for std, aliases in name_db.items():
            std_upper = std.upper()
            aliases_upper = [str(a).upper() for a in aliases]
            r3_name = r2_name.upper()

            # 综合判断
            if (r1_name in aliases or r1_name == std or 
                r2_name in aliases or r2_name == std or 
                r3_name in aliases_upper or r3_name == std_upper):
                matched_std = std
                break

        # 规则7：匹配成功后的操作
        if matched_std:
            for item in [tvg_id, tvg_name, display_name]:
                if item and item not in name_db[matched_std]:
                    name_db[matched_std].append(item)
            
            # 规则9：加后缀补偿
            suffixes = ["HD", ".cn@SD", ".cn@HD", " (2160p)", " (720p)", " (1080p)"]
            for s in suffixes:
                suffix_alias = f"{matched_std}{s}"
                if suffix_alias not in name_db[matched_std]:
                    name_db[matched_std].append(suffix_alias)
            
            name_db[matched_std] = list(set(name_db[matched_std]))
            matched_count += 1
        
    # 保存结果
    with open(NAME_JSON, 'w', encoding='utf-8') as f:
        json.dump(name_db, f, indent=2, ensure_ascii=False)

    # 生成任务报告
    print(f"================ 任务报告 ================")
    print(f"标准名称库基数: {len(name_db)} 个")
    print(f"源文件别名总数: {total_count}")
    print(f"成功识别数量: {matched_count}")
    print(f"未被识别数量: {total_count - matched_count}")
    print(f"总计执行耗时: {time.time() - start_time:.2f} 秒")
    print(f"==========================================")

if __name__ == "__main__":
    run_name_processor()