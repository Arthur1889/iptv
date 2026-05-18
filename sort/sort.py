import os
import sys
import re
import json
import time
import urllib.request

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)

# 定义核心路径
SOURCES_JSON_PATH = os.path.join(PARENT_DIR, "sources.json")
NAME_JSON_PATH = os.path.join(CURRENT_DIR, "name.json")
GROUP_JSON_PATH = os.path.join(CURRENT_DIR, "group.json")
UNKNOWN_JSON_PATH = os.path.join(CURRENT_DIR, "unknown.json")
CACHE_FILE_PATH = os.path.join(CURRENT_DIR, "sources_cache.json")

VALID_GROUPS = [
    "4K频道", "央视频道", "地方卫视", "港澳台", "山东频道", "上海频道", 
    "地方频道", "数字频道", "影视频道", "影视轮播", "歌曲及音乐MV", 
    "纪录纪实", "娱乐频道", "少儿动画", "体育赛事", "外语频道", "综合频道"
]

def clean_dirty_keys(name_str):
    """
    🧹 强力洗脏：瞬间剔除老代码大模型留在 JSON 里的 {"标准名":", [", \\ 等碎骨头
    """
    if not name_str:
        return ""
    name_str = re.sub(r'\{.*?:', '', name_str)
    name_str = re.sub(r'[\[\]\{\}\"\']', '', name_str)
    return name_str.replace('\\', '').strip()

def load_raw_channel_names():
    """
    💾 稳健的本地 M3U 缓存机制
    """
    if os.path.exists(CACHE_FILE_PATH) and os.path.getsize(CACHE_FILE_PATH) > 10:
        file_time = os.path.getmtime(CACHE_FILE_PATH)
        if time.time() - file_time < 86400:
            print("💾 命中本地缓存！正在快速载入去重名字...")
            try:
                with open(CACHE_FILE_PATH, 'r', encoding='utf-8') as cf:
                    data = json.load(cf)
                    if data and len(data) > 0:
                        return data
            except:
                pass

    if not os.path.exists(SOURCES_JSON_PATH):
        print(f"❌ 错误：未在上级目录找到 sources.json 文件")
        return []
        
    print("⏳ 开始从 sources.json 的网络源中批量解析原始名称...")
    with open(SOURCES_JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    urls = data.get("urls", [])
    if isinstance(urls, str): urls = [urls]
    
    raw_names = set()
    for url in set(urls):
        try:
            req = urllib.request.Request(url.strip(), headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=12) as r:
                m3u_text = r.read().decode('utf-8', errors='ignore')
            for line in m3u_text.splitlines():
                if line.strip().startswith("#EXTINF:"):
                    display_name = line.split(",")[-1].strip()
                    if display_name and not re.search(r'更新时间|爱琴海|画沙|202\d-\d', display_name):
                        raw_names.add(display_name)
        except Exception:
            pass
            
    result_list = list(raw_names)
    print(f"📡 成功联网解析并提取出物理去重频道共: {len(result_list)} 个。")
    
    with open(CACHE_FILE_PATH, 'w', encoding='utf-8') as cf:
        json.dump(result_list, cf, indent=2, ensure_ascii=False)
    return result_list

def regex_classify_channel(raw_name):
    """
    ⚙️ 纯本地高性能正则规则引擎
    """
    name = raw_name.upper().strip()
    
    # 1. 影视轮播、音乐MV
    if re.search(r'专场|合集|点播|精选|首|小时|串烧|视频|音乐|歌台|演唱会|歌曲|D|MV', name):
        if re.search(r'歌|音乐|唱|DJ|MV', name):
            return raw_name, "歌曲及音乐MV"
        return raw_name, "影视轮播"

    # 2. 4K频道
    if '4K' in name or '8K' in name:
        std = re.sub(r'(4K|8K|HD|FHD|高清|超清|\s)', '', raw_name)
        return std, "4K频道"

    # 3. 央视频道
    if 'CCTV' in name or '中央' in name:
        match = re.search(r'CCTV[-_]*\s*(\d+|NEWS|体育|电影|电视剧|纪实|科教|戏曲|社会|少儿|音乐|国防|农业|乡村|经典|健康|风云|兵器|台球|高尔夫|文化)', name, re.I)
        if match:
            return f"CCTV-{match.group(1)}", "央视频道"
        return "CCTV-综合", "央视频道"

    # 4. 地方卫视
    if '卫视' in name:
        std = re.sub(r'(HD|FHD|高清|超清|频道|\s)', '', raw_name)
        return std, "地方卫视"

    # 5. 港澳台系列
    if re.search(r'翡翠|明珠|凤凰|本港|TVB|HBO|CNBC|CNN|BBC|DISCOVERY|FOX|MOVI|KBS|NHK|港|澳|台', name):
        std = re.sub(r'(HD|FHD|高清|超清|\s)', '', raw_name)
        return std, "港澳台"

    # 6. 少儿动画
    if re.search(r'卡通|少儿|动漫|动画|金鹰|炫动|卡酷', name):
        return re.sub(r'(HD|高清|\s)', '', raw_name), "少儿动画"

    # 7. 体育赛事
    if re.search(r'体育|五星|劲爆|足球|篮球|台球|赛车', name):
        return re.sub(r'(HD|高清|\s)', '', raw_name), "体育赛事"

    # 8. 地域划分
    if '山东' in name or '齐鲁' in name:
        return raw_name, "山东频道"
    if '上海' in name or '东方' in name:
        return raw_name, "上海频道"

    # 9. 影视/纪录
    if re.search(r'纪录|纪实|探索|地理|求索', name):
        return re.sub(r'(HD|高清|\s)', '', raw_name), "纪录纪实"
    if re.search(r'电影|剧场|影院|影视|连续剧|剧', name):
        return re.sub(r'(HD|高清|\s)', '', raw_name), "影视频道"

    # 10. 常规地方台
    if re.search(r'新闻|综合|公共|生活|科教|都市|文礼|文旅', name):
        return re.sub(r'(HD|高清|\s)', '', raw_name), "地方频道"

    return None, None

def main():
    print("==========================================================")
    print(" 🚀 字典算法自愈系统启动（100%本地，已绝育400错误）")
    print("==========================================================")
    
    raw_names = load_raw_channel_names()
    if not raw_names:
        print("❌ 错误：未获取到有效原始数据！")
        return

    # 自动洗刷历史损坏数据
    name_repo = {}
    if os.path.exists(NAME_JSON_PATH):
        try:
            with open(NAME_JSON_PATH, 'r', encoding='utf-8') as f:
                old = json.load(f)
                name_repo = {clean_dirty_keys(k): clean_dirty_keys(v) for k, v in old.items() if clean_dirty_keys(k)}
        except: pass

    group_repo = {}
    if os.path.exists(GROUP_JSON_PATH):
        try:
            with open(GROUP_JSON_PATH, 'r', encoding='utf-8') as f:
                old = json.load(f)
                group_repo = {clean_dirty_keys(k): clean_dirty_keys(v) for k, v in old.items() if clean_dirty_keys(k)}
        except: pass

    unknown_list = []
    if os.path.exists(UNKNOWN_JSON_PATH):
        try:
            with open(UNKNOWN_JSON_PATH, 'r', encoding='utf-8') as f:
                old = json.load(f)
                unknown_list = [clean_dirty_keys(x) for x in old if clean_dirty_keys(x)]
        except: pass

    # 建立现存资产索引，实施精准增量过滤
    existing_keys = set(group_repo.keys())
    for std_k, aliases in name_repo.items():
        existing_keys.add(std_k.upper())
        for a in aliases.split(","):
            existing_keys.add(a.strip().upper())
    for uk in unknown_list:
        existing_keys.add(uk.upper())

    # 过滤出真正需要处理的新词
    filtered_names = [n for n in raw_names if clean_dirty_keys(n).upper() not in existing_keys]
    
    total_new = len(filtered_names)
    print(f"🧬 检测到本地已安全收录大部分频道。本次新增未入库怪名: {total_new} 个。")
    
    if total_new == 0:
        # 如果没有新增，直接用干净的结构重写一次，把以前留存的脏格式彻底重置掉
        with open(NAME_JSON_PATH, 'w', encoding='utf-8') as f: json.dump(name_repo, f, indent=2, ensure_ascii=False)
        with open(GROUP_JSON_PATH, 'w', encoding='utf-8') as f: json.dump(group_repo, f, indent=2, ensure_ascii=False)
        with open(UNKNOWN_JSON_PATH, 'w', encoding='utf-8') as f: json.dump(unknown_list, f, indent=2, ensure_ascii=False)
        print("✨ 本地字典当前 100% 纯净，历史脏数据清洗完毕，已全部就位。")
        return

    success_count = 0
    isolated_count = 0

    for raw_name in filtered_names:
        pure_raw = clean_dirty_keys(raw_name)
        if not pure_raw: continue
            
        std_name, group_name = regex_classify_channel(pure_raw)
        
        if std_name and group_name:
            success_count += 1
            if std_name in name_repo:
                current_aliases = [a.strip() for a in name_repo[std_name].split(",")]
                if pure_raw not in current_aliases and pure_raw != std_name:
                    name_repo[std_name] = f"{name_repo[std_name]}, {pure_raw}"
            else:
                if pure_raw != std_name:
                    name_repo[std_name] = pure_raw
            group_repo[pure_raw] = group_name
        else:
            isolated_count += 1
            if pure_raw not in unknown_list:
                unknown_list.append(pure_raw)

    # 数据全量回写落地
    with open(NAME_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(name_repo, f, indent=2, ensure_ascii=False)
    with open(GROUP_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(group_repo, f, indent=2, ensure_ascii=False)
    with open(UNKNOWN_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(unknown_list, f, indent=2, ensure_ascii=False)

    print("==========================================================")
    print(f"🎉 跑数大捷！新精确分类: {success_count} 个 | 独立隔离至 unknown.json: {isolated_count} 个。")
    print("==========================================================")

if __name__ == "__main__":
    main()