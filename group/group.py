import os
import sys
import re
import json
import time
import webbrowser
from tkinter import Tk
from collections import OrderedDict

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
URL_FILE_PATH = os.path.join(CURRENT_DIR, "url")
GROUP_JSON_PATH = os.path.join(CURRENT_DIR, "group.json")
HTML_SAVE_DIR = os.path.join(CURRENT_DIR, "html_cache")
CACHE_META_PATH = os.path.join(CURRENT_DIR, ".url_cache_meta")

# 第一步的完美圈地成果
ZONE_TEXT_PATH = os.path.join(CURRENT_DIR, "extracted_zones.txt")

PROVINCES = [
    "北京", "上海", "天津", "重庆", "广东", "山东", "浙江", "江苏", "安徽", "福建", 
    "江西", "湖北", "河南", "河北", "山西", "吉林", "辽宁", "广西", "四川", "贵州", 
    "云南", "陕西", "甘肃", "青海", "宁夏", "新疆", "海南", "西藏", "黑龙江", "内蒙古"
]

def get_target_urls():
    if not os.path.exists(URL_FILE_PATH): return []
    urls = []
    with open(URL_FILE_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and line.startswith("http"):
                urls.append(re.sub(r'[；;，,\s\t\n\r]+$', '', line))
    return urls

def load_cache_meta():
    if os.path.exists(CACHE_META_PATH):
        try:
            with open(CACHE_META_PATH, 'r', encoding='utf-8') as f: return json.load(f)
        except Exception: return {}
    return {}

def save_cache_meta(meta):
    try:
        with open(CACHE_META_PATH, 'w', encoding='utf-8') as f: json.dump(meta, f, indent=2, ensure_ascii=False)
    except Exception: pass

def get_url_filename(url, index):
    clean_url = re.sub(r'[\/\\\:\*\?\"\<\>\|]', '_', url)
    return f"cache_{index}_{clean_url[-50:] if len(clean_url) > 50 else clean_url}.txt"

def get_clipboard_text():
    try:
        root = Tk()
        root.withdraw()
        text = root.clipboard_get()
        root.destroy()
        return text
    except Exception: return ""

def fetch_clipboard_or_local_cache(target_url, index, cache_meta, total_count):
    local_filename = get_url_filename(target_url, index)
    local_path = os.path.join(HTML_SAVE_DIR, local_filename)
    if os.path.exists(local_path):
        with open(local_path, 'r', encoding='utf-8') as f: return f.read(), False
    print(f"\n🎬 ===================== [ 进度: {index} / {total_count} ] =====================")
    print(f"  🌍 正在拉起网页: {target_url}")
    webbrowser.open(target_url, new=2)
    input(f"  👉 完成【Ctrl+A】+【Ctrl+C】后，请回到终端【敲击回车】...")
    clipboard_content = get_clipboard_text()
    if clipboard_content:
        with open(local_path, 'w', encoding='utf-8') as f: f.write(clipboard_content)
        cache_meta[target_url] = time.time()
    return clipboard_content, True

# =====================================================================
# 🌟 第一步：100% 保持完美的物理圈地逻辑不变
# =====================================================================
def step1_extract_zone(target_urls, cache_meta):
    print("\n▶️ 正在执行【第一步】：高保真物理圈地固化...")
    total_count = len(target_urls)
    extracted_lines = []

    for idx, t_url in enumerate(target_urls, start=1):
        text_content, _ = fetch_clipboard_or_local_cache(t_url, idx, cache_meta, total_count)
        if not text_content: continue

        flat_text = " ".join(text_content.splitlines())
        page_keyword = "CCTV" if "cctv" in t_url.lower() else ""
        if not page_keyword:
            for prov in PROVINCES:
                if prov.lower() in t_url.lower():
                    page_keyword = prov
                    break
        
        start_flags = [f"{page_keyword}台节目表", f"{page_keyword}节目表", f"{page_keyword}综合"]
        start_pos = -1
        for flag in start_flags:
            if page_keyword and flag in flat_text:
                start_pos = flat_text.index(flag) + len(flag)
                break
                
        try:
            end_pos = flat_text.index("热门电视台")
            if start_pos == -1:
                start_pos = flat_text[:end_pos].rindex("节目表") + len("节目表")
            pure_zone = flat_text[start_pos:end_pos].strip()
            extracted_lines.append(f"{t_url}|||{pure_zone}")
        except ValueError:
            extracted_lines.append(f"{t_url}|||")

    with open(ZONE_TEXT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(extracted_lines))
    print(f"🎉 【第一步完成】圈地文本固化成功。")

# =====================================================================
# 🌟 第二步：高精度标签分类（卫视独立分组升级版）
# =====================================================================
def step2_clean_channels():
    print("\n▶️ 正在执行【第二步】：高精度网址标签分类（卫视独立分组）...")
    if not os.path.exists(ZONE_TEXT_PATH): return

    final_group_dict = OrderedDict()

    # 标准电视台核心提取器：死死咬住中英数混合字符加常见的台名后缀
    channel_extract_pattern = re.compile(
        r'(CCTV[-a-zA-Z0-9\+]+(?:\s[\u4e00-\u9fa5]+)?|'
        r'CETV-?\d|'
        r'CGTN[\u4e00-\u9fa5\s\w\+]*频道|'
        r'XJTV-?\d+|BTV-?\d+|TVS-?\d+|QTV[a-zA-Z0-9\u4e00-\u9fa5]*|XM6|' 
        r'[\u4e00-\u9fa5a-zA-Z0-9\-\+]{2,12}?(?:卫视|频道|一套|二套|三套|综合|财经|综艺|体育|电影|电视剧|新闻|少儿|音乐|国际|文体|生活|娱乐|城市|纪实|财富|故事|法制|农民|农科)|'
        r'快乐垂钓|四海钓鱼|湖南快乐购|先锋兵羽|好易购|点掌财经|幸福彩|快手体育|东方购物|金鹰纪实|文物宝库|武术世界|武林风|先锋乒羽)'
    )

    with open(ZONE_TEXT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "|||" not in line: continue
            t_url, pure_zone = line.split("|||", 1)
            if not pure_zone.strip(): continue

            # 🪐 1. 根据这一行属于哪个 URL，提前给它划分基础的【默认分组】
            url_lower = t_url.lower()
            default_group = "地方频道"
            
            if "cctv" in url_lower: default_group = "央视频道"
            elif "shaoer" in url_lower: default_group = "少儿频道"
            elif "tiyu" in url_lower: default_group = "体育频道"
            elif "gangaotai" in url_lower: default_group = "港澳台"
            else:
                for prov in PROVINCES:
                    if prov.lower() in url_lower:
                        default_group = f"{prov}频道"
                        break

            # 🪐 2. 在这一行黄金区里抠出所有的独立台
            raw_matches = channel_extract_pattern.findall(pure_zone)
            
            for chunk_str in raw_matches:
                chunk_str = chunk_str.strip()
                if not chunk_str or len(chunk_str) <= 1 or chunk_str.isdigit(): continue
                
                ch_clean = chunk_str.split(" ")[0]
                ch_clean = re.sub(r'(直播|在线|高清|超清|节目表)$', '', ch_clean, flags=re.I).strip()
                if not ch_clean or len(ch_clean) <= 1: continue

                # 🪐 3. 🧠 高精度分类（通过重新编排 if-elif 优先级实现卫视完全剥离）
                name_up = ch_clean.upper()
                group_clean = default_group

                # 👑 核心调整：卫视拦截优先级提高！
                if 'CCTV' in name_up or 'CETV' in name_up or 'CGTN' in name_up or '中央台' in name_up:
                    group_clean = "央视频道"
                elif '卫视' in name_up and '朝鲜语' not in name_up:
                    group_clean = "卫视频道"  # 🌟 只要名字带卫视，立刻脱离省份分组，单独列为卫视频道
                elif '少儿' in name_up or '卡通' in name_up:
                    group_clean = "少儿频道"
                elif '体育' in name_up or '五星体育' in name_up:
                    group_clean = "体育频道"
                elif re.search(r'翡翠台|明珠台|凤凰|TVB|HBO|CNBC|CNN|BBC|DISCOVERY|FOX|中天|三立|纬来|台视|无线台|HOY|澳ia卫视', name_up):
                    group_clean = "港澳台"

                # 严格保序入队
                if ch_clean not in final_group_dict:
                    final_group_dict[ch_clean] = group_clean

    # 🌟 队尾追加 CCTV 保底资产
    for i in range(1, 18):
        if f"CCTV-{i}" not in final_group_dict: final_group_dict[f"CCTV-{i}"] = "央视频道"
    if "CCTV-5+" not in final_group_dict: final_group_dict["CCTV-5+"] = "央视频道"

    with open(GROUP_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(final_group_dict, f, indent=2, ensure_ascii=False)
    print(f"🎉 【第二步完成】独立的【卫视频道】分组已完全剥离，group.json 成功刷新！")

def main():
    if not os.path.exists(HTML_SAVE_DIR): os.makedirs(HTML_SAVE_DIR)
    cache_meta = load_cache_meta()
    target_urls = get_target_urls()
    if not target_urls: return

    step1_extract_zone(target_urls, cache_meta)
    step2_clean_channels()
    save_cache_meta(cache_meta)

if __name__ == "__main__":
    main()