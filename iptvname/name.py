import json
import re
import os
import time

# 配置路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)
NAME_JSON = os.path.join(BASE_DIR, 'name.json')
GROUP_JSON = os.path.join(PARENT_DIR, 'group.json')
SOURCES_CACHE = os.path.join(PARENT_DIR, 'sources_cache.txt')

# 语义补偿库：处理拼音/英文缩写到标准名的映射
SEMANTIC_DB = {
    "SICHUAN卫视": "四川卫视",
    "SIPINGTV": "四平新闻综合",
    "ANDO": "安多卫视",
    "ZHEJIANG": "浙江卫视",
    "CCTV1": "CCTV1",
    "CGTN": "CGTN"
}

def clean_string(s):
    """规则2：去除干扰字符，保留纯净名称"""
    patterns = [r'\.cn@\w+', r'\.\w+@\w+', r'\(.*?\)', r'\[.*?\]', r'-', r'HD', r'SD']
    for p in patterns:
        s = re.sub(p, '', s)
    return s.strip()

def load_json(path):
    if not os.path.exists(path): return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def run_name_processor():
    start_time = time.time()
    
    # 第一步：继承 group.json 标准名
    groups = load_json(GROUP_JSON)
    name_db = {k: [k] for k in groups.keys()} 
    
    # 第二步：合并原有 name.json
    existing = load_json(NAME_JSON)
    for k, v in existing.items():
        if k not in name_db: name_db[k] = []
        name_db[k].extend(v)

    # 第三步：读取 sources_cache，带健壮性校验
    if not os.path.exists(SOURCES_CACHE):
        print(f"错误: 找不到 {SOURCES_CACHE}")
        return

    with open(SOURCES_CACHE, 'r', encoding='utf-8') as f:
        lines = [line.strip() for line in f if line.strip()]

    matched_count = 0
    total_count = len(lines)

    for line in lines:
        parts = line.split(',')
        if len(parts) < 3: continue # 过滤格式错误行
        
        tvg_id, tvg_name, display_name = parts[0], parts[1], parts[2]
        
        # 规则1：中文优先级
        raw_names = [display_name, tvg_id, tvg_name]
        target_name = next((n for n in raw_names if re.search(r'[\u4e00-\u9fa5]', n)), raw_names[0])
        
        found = False
        clean_name = clean_string(target_name)
        
        # 匹配逻辑
        for std_name, aliases in name_db.items():
            # 语义补偿匹配
            comp_name = SEMANTIC_DB.get(clean_name.upper(), clean_name)
            
            if comp_name in aliases or clean_name.upper() == std_name.upper():
                name_db[std_name].extend([tvg_id, tvg_name, display_name])
                
                # 规则9: 自动补全后缀
                suffixes = ["HD", ".cn@SD", ".cn@HD", " (2160p)", " (720p)", " (1080p)"]
                for s in suffixes:
                    name_db[std_name].append(std_name + s)
                
                name_db[std_name] = list(set(name_db[std_name])) # 去重
                matched_count += 1
                found = True
                break
        
    # 规则8 & 清洗写入
    final_db = {k.replace("</br>", ""): v for k, v in name_db.items()}
    with open(NAME_JSON, 'w', encoding='utf-8') as f:
        json.dump(final_db, f, indent=2, ensure_ascii=False)

    print(f"--- 任务报告 ---")
    print(f"处理总数: {total_count}")
    print(f"成功识别: {matched_count}")
    print(f"未识别数: {total_count - matched_count}")
    print(f"耗时: {time.time() - start_time:.2f}秒")

if __name__ == "__main__":
    run_name_processor()