import os
import sys
import re
import json

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)

# 核心路径
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
    if not name_str: return ""
    name_str = re.sub(r'\{.*?:', '', name_str)
    name_str = re.sub(r'[\[\]\{\}\"\']', '', name_str)
    return name_str.replace('\\', '').strip()

def regex_classify_channel(raw_name):
    """
    🧠 5条核心特征树+工业级语义清洗（产出绝对标准的 Key 和分组）
    """
    name = raw_name.upper().strip()
    
    # ─── 特征 (1) & (5)：带首、直播室的归类 ───
    if '首' in name or re.search(r'串烧|DJ|MV|演唱会|慢摇', name):
        return "歌曲轮播", "歌曲及音乐MV"
    if '直播室' in name or re.search(r'美女|热舞|颜值|TIKTOK|主播', name):
        return "网络娱乐直播", "娱乐频道"

    # ─── 特征 (2)：带专场的一般是电影、娱乐或综合 ───
    if '专场' in name or re.search(r'合集|点播|精选|小时|视频|解说|短剧', name):
        if re.search(r'电影|影迷|CHC|剧场', name):
            return "电影轮播专场", "影视频道"
        if re.search(r'歌|音乐|唱', name):
            return "歌曲轮播", "歌曲及音乐MV"
        return "综艺娱乐专场", "影视轮播"

    # ─── 特征 (3)：带影视的一般是影视频道 ───
    if '影视' in name or re.search(r'电影|剧场|影院|连续剧|剧|影迷|CHC|邵氏', name):
        # 排除CCTV风云剧场等
        if 'CCTV' not in name:
            std = re.sub(r'(HD|FHD|高清|超清|频道|NOT\s*24/7|\d+P|\s)', '', raw_name)
            return std.strip(), "影视频道"

    # ─── 特征 (4)：带CCTV的肯定是央视频道 (深度标准化) ───
    if 'CCTV' in name or '中央' in name:
        match = re.search(r'CCTV[-_]*\s*(\d+|NEWS|体育|电影|电视剧|纪实|科教|戏曲|社会|少儿|音乐|国防|农业|乡村|经典|健康|风云|兵器|台球|高尔夫|文化|精品|娱乐|地理)', name)
        if match:
            sub = match.group(1)
            mapping = {"1":"1 综合", "2":"2 财经", "3":"3 综艺", "4":"4 中文国际", "5":"5 体育", "6":"6 电影", "7":"7 国防军事", "8":"8 电视剧", "9":"9 纪录", "10":"10 科教", "11":"11 戏曲", "12":"12 社会与法", "13":"13 新闻", "14":"14 少儿", "15":"15 音乐", "16":"16 奥林匹克", "17":"17 农业农村"}
            return f"CCTV-{mapping.get(sub, sub)}", "央视频道"
        for kw in ["剧场", "故事", "指南", "戏曲", "台球", "兵器", "地理", "精品", "文化"]:
            if kw in raw_name:
                return f"CCTV-{kw}", "央视频道"
        return "CCTV-1 综合", "央视频道"

    # ─── 扩展过滤：地方卫视清洗 ───
    if '卫视' in name:
        std = re.sub(r'(HD|FHD|高清|超清|频道|国家|NOT\s*24/7|\d+P|\([^\)]*\))', '', raw_name)
        return std.strip(), "地方卫视"

    # ─── 扩展过滤：港澳台系列 ───
    if re.search(r'翡翠|明珠|凤凰|本港|TVB|HBO|CNBC|CNN|BBC|DISCOVERY|FOX|MOVI|KBS|NHK|NETFLIX|中天|三立|纬来|台视|无线新聞', name):
        if not re.search(r'路|大道|高速|监控', name):
            std = re.sub(r'(HD|FHD|高清|超清|NOT\s*24/7|\d+P|\([^\)]*\))', '', raw_name)
            return std.strip(), "港澳台"

    # ─── 基础垂直分类 ───
    if re.search(r'卡通|少儿|动漫|动画|金鹰|炫动|卡酷|BABY|儿童', name):
        return re.sub(r'(HD|高清|NOT\s*24/7|\d+P|\s)', '', raw_name).strip(), "少儿动画"

    if re.search(r'体育|五星|劲爆|足球|篮球|台球|赛车|格斗|赛事|咪咕|NBA', name):
        return re.sub(r'(HD|高清|NOT\s*24/7|\d+P|\s)', '', raw_name).strip(), "体育赛事"

    if re.search(r'纪录|纪实|探索|地理|求索|DOCUMENTARY', name):
        return re.sub(r'(HD|高清|NOT\s*24/7|\d+P|\s)', '', raw_name).strip(), "纪录纪实"

    if re.search(r'新闻|综合|公共|生活|科教|都市|文旅|民生|城市', name):
        if not re.search(r'路|大道|高速|监控', name):
            return re.sub(r'(HD|高清|NOT\s*24/7|\d+P|\s)', '', raw_name).strip(), "地方频道"

    return None, None

def main():
    print("==========================================================")
    print(" 🚀 字典树标准对齐化升级版 sort.py 启动 ")
    print("==========================================================")
    
    if not os.path.exists(CACHE_FILE_PATH):
        print("❌ 错误：未找到本地缓存文件 sources_cache.json")
        return
        
    with open(CACHE_FILE_PATH, 'r', encoding='utf-8') as cf:
        raw_names = json.load(cf)

    # 彻底初始化全新的资产库
    name_repo = {}
    group_repo = {}
    unknown_list = []

    success_count = 0
    isolated_count = 0

    for raw_name in raw_names:
        pure_raw = clean_dirty_keys(raw_name)
        if not pure_raw: continue
            
        std_name, group_name = regex_classify_channel(pure_raw)
        
        if std_name and group_name:
            success_count += 1
            
            # 🌟 核心调正：以干净的标准名作为 Key！
            if std_name in name_repo:
                current_aliases = [a.strip() for a in name_repo[std_name].split(",")]
                if pure_raw not in current_aliases and pure_raw != std_name:
                    name_repo[std_name] = f"{name_repo[std_name]}, {pure_raw}"
            else:
                if pure_raw != std_name:
                    name_repo[std_name] = pure_raw
                    
            # 映射表：让所有野生原名完美找到对应标准分组
            group_repo[pure_raw] = group_name
        else:
            isolated_count += 1
            if pure_raw not in unknown_list:
                unknown_list.append(pure_raw)

    # 数据重组后写盘
    with open(NAME_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(name_repo, f, indent=2, ensure_ascii=False)
    with open(GROUP_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(group_repo, f, indent=2, ensure_ascii=False)
    with open(UNKNOWN_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(unknown_list, f, indent=2, ensure_ascii=False)

    print("==========================================================")
    print(f"🎉 跑数大捷！资产库完美重置对齐。")
    print(f"✅ 标准Key映射捕获: {success_count} 个")
    print(f"⚠️  不确定规则的怪名已放入未知隔离区 unknown.json: {isolated_count} 个")
    print("==========================================================")

if __name__ == "__main__":
    main()