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

# 第一步截取完的黄金原生区临时存储文件
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
    print(f"  💡 [提示] 请直接在网页里：【Ctrl+A】全选 -> 【Ctrl+C】复制")
    input(f"  👉 完成后，请回到终端【敲击回车(Enter)】解锁存取...")
    
    clipboard_content = get_clipboard_text()
    if clipboard_content:
        with open(local_path, 'w', encoding='utf-8') as f: f.write(clipboard_content)
        cache_meta[target_url] = time.time()
        print(f"  💾 [快照已成功落地]: {local_filename}")
    return clipboard_content, True

# =====================================================================
# 🌟 第一步：绝对圈地。寻找到核心电台区间，按省份分行写入临时文件
# =====================================================================
def step1_extract_zone(target_urls, cache_meta):
    print("\n▶️ 正在执行【第一步】：物理截取核心正规军内容...")
    total_count = len(target_urls)
    extracted_lines = []

    for idx, t_url in enumerate(target_urls, start=1):
        text_content, _ = fetch_clipboard_or_local_cache(t_url, idx, cache_meta, total_count)
        if not text_content: continue

        # 🪐 1. 扁平化处理
        flat_text = " ".join(text_content.splitlines())

        # 🪐 2. 🧠 铁腕圈地逻辑：
        # 我们发现无论网页怎么黏连，“黄金内容区”永远在快捷标签末尾的“少儿”和“热门电视台”之间。
        # 所以先定位“热门电视台”，再从“热门电视台”往前倒找离它最近的那个“节目表”作为起点！
        try:
            end_pos = flat_text.index("热门电视台")
            front_text = flat_text[:end_pos]
            
            # 从热门电视台往前找，最后一次出现的“节目表”，就是最纯净的核心起点！
            start_pos = front_text.rindex("节目表") + len("节目表")
            
            pure_zone = front_text[start_pos:].strip()
            
            # 用 ||| 把网址和圈出的核心数据分隔开，存入单行
            extracted_lines.append(f"{t_url}|||{pure_zone}")
            print(f"  ✅ 节点 #{idx} 圈地成功 -> 已安全截获核心文本。")
        except ValueError:
            print(f"  ❌ 错误：节点 #{idx} 无法精确定位边界，本行强制留空保序。")
            extracted_lines.append(f"{t_url}|||")

    # 固化第一步结果
    with open(ZONE_TEXT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(extracted_lines))
    print(f"🎉 【第一步完成】黄金核心文本已成功分行写入: {os.path.basename(ZONE_TEXT_PATH)}")


# =====================================================================
# 🌟 第二步：频道名切分与清理
# =====================================================================
def step2_clean_channels():
    print("\n▶️ 正在执行【第二步】：从固化文件中切分频道名并纯净归类...")
    if not os.path.exists(ZONE_TEXT_PATH):
        print("❌ 错误：未找到第一步的临时文本，无法执行第二步。")
        return

    final_group_dict = OrderedDict()
    split_suffixes = ["卫视", "频道", "一套", "二套", "三套", "综合", "财经", "综艺", "体育", "电影", "电视剧", "新闻", "少儿", "音乐"]

    with open(ZONE_TEXT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "|||" not in line: continue
            t_url, pure_zone = line.split("|||", 1)
            if not pure_zone.strip(): continue

            # 🪐 1. 爆破式将长句炸成数组
            processed_zone = pure_zone
            for sfx in split_suffixes:
                processed_zone = processed_zone.replace(sfx, f"{sfx}|||")
            
            raw_chunks = processed_zone.split("|||")
            
            # 🪐 2. 净化台名并保序录入
            for chunk in raw_chunks:
                chunk_str = chunk.strip()
                if chunk_str and 2 <= len(chunk_str) <= 15:
                    ch_clean = chunk_str.split(" ")[0]
                    junk = [r'直播', r'在线', r'高清', r'超清', r'节目表']
                    for pattern in junk:
                        ch_clean = re.sub(pattern, '', ch_clean, flags=re.I)
                    
                    # 剥离由于前部截断残留在台名前面的碎字噪声
                    bad_prefixes = [r'^闻综合', r'^方购物', r'^动电视', r'^共新闻', r'^视娱乐', r'^生休闲', r'^视购物', r'^化影视', r'^育科技', r'^治天地', r'^星体育', r'^经生活', r'^儿科教', r'^活服务', r'^水影视娱乐', r'^济资讯', r'^体博览', r'^儿家庭', r'^技生活', r'^济旅游', r'^动戏曲', r'^乐思购', r'^共农村', r'^化旅游', r'^女儿童', r'^新干线', r'^黔南', r'^黔西南', r'^育休闲', r'^资讯', r'^[^\u4e00-\u9fa5a-zA-Z0-9]+']
                    for prefix in bad_prefixes:
                        ch_clean = re.sub(prefix, '', ch_clean)
                    ch_clean = ch_clean.strip()

                    if not ch_clean or len(ch_clean) <= 1: continue

                    # 精准分类映射
                    name_up = ch_clean.upper()
                    url_hint = t_url.lower()
                    if 'CCTV' in name_up or '中央台' in name_up or 'CETV' in name_up or ('中央' in name_up and '广播' not in name_up):
                        group_clean = "央视频道"
                    elif re.search(r'翡翠|明珠|凤凰|本港|TVB|HBO|CNBC|CNN|BBC|DISCOVERY|FOX|中天|三立|纬来|台视|无线|HOY|港|澳|台', name_up) or "gangaotai" in url_hint:
                        group_clean = "港澳台"
                    elif '少儿' in name_up or '卡通' in name_up or '动漫' in name_up or '动画' in name_up or "shaoer" in url_hint:
                        group_clean = "少儿频道"
                    elif '体育' in name_up or '足球' in name_up or '篮球' in name_up or '赛事' in name_up or '五星' in name_up or "tiyu" in url_hint:
                        group_clean = "体育频道"
                    elif '卫视' in name_up:
                        group_clean = "卫视频道"
                    else:
                        group_clean = "地方频道"
                        for prov in PROVINCES:
                            if prov in ch_clean or prov.lower() in url_hint:
                                group_clean = f"{prov}频道"
                                break

                    # 严格按 url 文件从上到下的读取顺序，首次出现即卡死位置
                    if ch_clean not in final_group_dict:
                        final_group_dict[ch_clean] = group_clean

    # 🌟 队尾追加核心央视保底资产
    for i in range(1, 18):
        if f"CCTV-{i}" not in final_group_dict: final_group_dict[f"CCTV-{i}"] = "央视频道"
    if "CCTV-5+" not in final_group_dict: final_group_dict["CCTV-5+"] = "央视频道"

    with open(GROUP_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(final_group_dict, f, indent=2, ensure_ascii=False)
    print(f"🎉 【第二步完成】所有清洗和归类结果已成功写入: {os.path.basename(GROUP_JSON_PATH)}")


def main():
    if not os.path.exists(HTML_SAVE_DIR): os.makedirs(HTML_SAVE_DIR)
    cache_meta = load_cache_meta()
    target_urls = get_target_urls()
    if not target_urls: return

    # 执行两步走战略
    step1_extract_zone(target_urls, cache_meta)
    step2_clean_channels()

    save_cache_meta(cache_meta)
    print("\n🚀 恭喜！报错完美修复，两步走战略顺利通关！")

if __name__ == "__main__":
    main()