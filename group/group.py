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

PROVINCES = [
    "北京", "上海", "天津", "重庆", "广东", "山东", "浙江", "江苏", "安徽", "福建", 
    "江西", "湖北", "河南", "河北", "山西", "吉林", "辽宁", "广西", "四川", "贵州", 
    "云南", "陕西", "甘肃", "青海", "宁夏", "新疆", "海南", "西藏", "黑龙江", "内蒙古"
]

def clean_channel_name(name):
    if not name: return ""
    name = str(name).strip()
    # 彻底切掉可能粘连在后面的空格或者网页残留尾巴
    name = name.split(" ")[0]
    junk = [r'直播', r'在线', r'高清', r'超清', r'频道', r'电视台']
    for pattern in junk:
        name = re.sub(pattern, '', name, flags=re.I)
    return name.strip()

def regex_classify_channel(clean_name, url_hint):
    name = clean_name.upper()
    url_hint = url_hint.lower()

    if 'CCTV' in name or '中央' in name or 'CETV' in name or '教育' in name or "cctv" in url_hint: 
        return "央视频道"
    if re.search(r'翡翠|明珠|凤凰|本港|TVB|HBO|CNBC|CNN|BBC|DISCOVERY|FOX|中天|三立|纬来|台视|无线|HOY|港|澳|台', name) or "gangaotai" in url_hint: 
        return "港澳台"
    if '少儿' in name or '卡通' in name or '动漫' in name or '动画' in name or "shaoer" in url_hint: 
        return "少儿频道"
    if '体育' in name or '足球' in name or '篮球' in name or '赛事' in name or '五星' in name or "tiyu" in url_hint: 
        return "体育频道"
    if '卫视' in name: 
        return "卫视频道"

    for prov in PROVINCES:
        if prov in clean_name or prov.lower() in url_hint: 
            return f"{prov}频道"

    return "地方频道"

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
    
    # 7天离线免打扰
    if os.path.exists(local_path):
        days_passed = (time.time() - cache_meta.get(target_url, 0)) / (24 * 3600)
        if days_passed < 7:
            print(f"  📦 [本地快照激活] #{index} 节点直接读取本地副本")
            with open(local_path, 'r', encoding='utf-8') as f: return f.read(), False

    print(f"\n🎬 ===================== [ 进度: {index} / {total_count} ] =====================")
    print(f"  🌍 正在拉起网页: {target_url}")
    webbrowser.open(target_url, new=2)
    
    print(f"  💡 [提示] 请直接在网页里：【Ctrl+A】全选 -> 【Ctrl+C】复制")
    input(f"  👉 完成后，请回到终端【敲击回车(Enter)】执行分拣...")
    
    clipboard_content = get_clipboard_text()
    if clipboard_content:
        with open(local_path, 'w', encoding='utf-8') as f: f.write(clipboard_content)
        cache_meta[target_url] = time.time()
        print(f"  💾 [快照已成功落地]: {local_filename}")
    return clipboard_content, True

def main():
    print("==========================================")
    print(" 🚀 不间断滚动捕获·高保真分拣引擎启动")
    print("==========================================")
    start_time = time.time()
    target_urls = get_target_urls()
    if not target_urls: return
    
    if not os.path.exists(HTML_SAVE_DIR): os.makedirs(HTML_SAVE_DIR)
    cache_meta = load_cache_meta()
    group_dict = OrderedDict()
    manual_op_count = 0
    total_count = len(target_urls)

    # 🌟 核心升级：滚雪球匹配正则，精准捕捉不带换行符、粘连在一块的电台大军
    # 能够精准从 “CCTV-1 综合CCTV-2 财经湖南卫视湖南经视频道” 文本中切分出独立的台
    channel_extract_pattern = re.compile(
        r'([a-zA-Z0-9\-\+]+卫视|[a-zA-Z0-9\-\+]+台|[a-zA-Z0-9\-\+]+频道|CCTV[-a-zA-Z0-9\+]+(?:\s[\u4e00-\u9fa5]+)?|[\u4e00-\u9fa5]+卫视|[\u4e00-\u9fa5]+频道|[\u4e00-\u9fa5]+一套|[\u4e00-\u9fa5]+二套|快乐垂钓|四海钓鱼|湖南快乐购)'
    )

    for idx, t_url in enumerate(target_urls, start=1):
        text_content, is_manual = fetch_clipboard_or_local_cache(t_url, idx, cache_meta, total_count)
        if is_manual: manual_op_count += 1
        if not text_content: continue

        # 🪐 1. 扁平化清洗，抹除所有多余的换行符，变成纯粹的不间断文本流
        flat_text = " ".join(text_content.splitlines())

        # 🪐 2. 精准定位“XX节目表”到“热门电视台”之间的核心数据区间
        start_match = re.search(r'[\u4e00-\u9fa5]+节目表', flat_text)
        end_match = re.search(r'热门电视台', flat_text)
        
        if not start_match:
            print(f"  ⚠️  注意：未能在缓存文本中匹配到“节目表”分水岭，跳过节点 #{idx}。")
            continue
            
        start_pos = start_match.end()
        end_pos = end_match.start() if end_match else len(flat_text)
        pure_zone = flat_text[start_pos:end_pos].strip()

        # 🪐 3. 运行滚动捕获正则，将粘连的文本切成独立的电台数组
        page_channels = channel_extract_pattern.findall(pure_zone)
        print(f"  📊 滚动切片成功 -> 收录本页纯净台数: {len(page_channels)} 个")
        
        for ch_name in page_channels:
            ch_clean = clean_channel_name(ch_name)
            if not ch_clean or len(ch_clean) <= 1: continue
            group_clean = regex_classify_channel(ch_clean, t_url)
            group_dict[ch_clean] = group_clean

    # 基准央视资产保底注入
    for i in range(1, 18): group_dict[f"CCTV-{i}"] = "央视频道"
    group_dict["CCTV-5+"] = "央视频道"

    with open(GROUP_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(group_dict, f, indent=2, ensure_ascii=False)

    save_cache_meta(cache_meta)
    duration = time.time() - start_time
    print(f"\n🎉 运行报告：大功告成！全口径滚动提取生效。大集群共计完美收录了 {len(group_dict)} 个优质频道。")

if __name__ == "__main__":
    main()