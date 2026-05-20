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
    name = name.split(" ")[0]  # 切掉尾部空格和网页解释
    
    # 🧹 强力扫除由于切片残余导致的残缺前缀噪声
    bad_prefixes = [r'^闻综合', r'^方购物', r'^动电视', r'^共新闻', r'^视娱乐', r'^生休闲', r'^视购物', r'^化影视', r'^育科技', r'^治天地', r'^星体育', r'^经生活', r'^儿科教', r'^活服务', r'^水影视娱乐', r'^济资讯', r'^体博览', r'^儿家庭', r'^技生活', r'^济旅游', r'^动戏曲', r'^乐思购', r'^共农村', r'^化旅游', r'^女儿童', r'^新干线', r'^黔南', r'^黔西南', r'^育休闲', r'^资讯', r'^[^\u4e00-\u9fa5a-zA-Z0-9]+']
    for prefix in bad_prefixes:
        name = re.sub(prefix, '', name)
        
    junk = [r'直播', r'在线', r'高清', r'超清', r'节目表']
    for pattern in junk:
        name = re.sub(pattern, '', name, flags=re.I)
    return name.strip()

def regex_classify_channel(clean_name, url_hint):
    name = clean_name.upper()
    url_hint = url_hint.lower()

    # 1. 严格限制：只有真正包含央视核心词的才归为央视
    if 'CCTV' in name or '中央台' in name or 'CETV' in name or ('中央' in name and '广播' not in name): 
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
        if prov in clean_name: 
            return f"{prov}频道"

    for prov in PROVINCES:
        if prov.lower() in url_hint:
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
    
    if os.path.exists(local_path):
        days_passed = (time.time() - cache_meta.get(target_url, 0)) / (24 * 3600)
        if days_passed < 7:
            print(f"  📦 [本地缓存激活] #{index} 节点直接载入本地快照。")
            with open(local_path, 'r', encoding='utf-8') as f: return f.read(), False

    print(f"\n🎬 ===================== [ 进度: {index} / {total_count} ] =====================")
    print(f"  🌍 正在拉起网页: {target_url}")
    webbrowser.open(target_url, new=2)
    
    print(f"  💡 [提示] 请直接在网页里：【Ctrl+A】全选 -> 【Ctrl+C】复制")
    input(f"  👉 完成后，请回到终端【敲击回车(Enter)】解锁分拣...")
    
    clipboard_content = get_clipboard_text()
    if clipboard_content:
        with open(local_path, 'w', encoding='utf-8') as f: f.write(clipboard_content)
        cache_meta[target_url] = time.time()
        print(f"  💾 [快照已固化]: {local_filename}")
    return clipboard_content, True

def main():
    print("==========================================")
    print(" 🚀 严格按读取顺序·爆破隔离分拣引擎启动")
    print("==========================================")
    start_time = time.time()
    target_urls = get_target_urls()
    if not target_urls: return
    
    if not os.path.exists(HTML_SAVE_DIR): os.makedirs(HTML_SAVE_DIR)
    cache_meta = load_cache_meta()
    
    # 🌟 核心保序：使用 OrderedDict 确保写入顺序完全对应 url 文件的读取顺序
    group_dict = OrderedDict()
    manual_op_count = 0
    total_count = len(target_urls)

    # 用来对粘连文本执行“解体”的独立后缀标记
    split_suffixes = ["卫视", "频道", "一套", "二套", "三套", "综合", "财经", "综艺", "体育", "电影", "电视剧", "新闻", "少儿", "音乐"]

    for idx, t_url in enumerate(target_urls, start=1):
        text_content, is_manual = fetch_clipboard_or_local_cache(t_url, idx, cache_meta, total_count)
        if is_manual: manual_op_count += 1
        if not text_content: continue

        # 🪐 1. 消除一切换行符障碍，整合为纯文本流
        flat_text = " ".join(text_content.splitlines())

        # 🪐 2. 物理截断：必须在执行任何 replace 爆破前，优先用最原始的文本锁定边界！
        boundary_match = re.search(r'少儿.*?([\u4e00-\u9fa5]+节目表)', flat_text)
        end_match = re.search(r'热门电视台', flat_text)
        
        if not boundary_match:
            print(f"  ⚠️  注意：未能在缓存文本中精确定位到“节目表”分水岭，跳过节点 #{idx}。")
            continue
            
        start_pos = boundary_match.end()
        end_pos = end_match.start() if end_match else len(flat_text)
        pure_zone = flat_text[start_pos:end_pos].strip()

        # 🪐 3. 爆破式分段算法：不用正则匹配，直接用核心词把长句炸开！
        processed_zone = pure_zone
        for sfx in split_suffixes:
            processed_zone = processed_zone.replace(sfx, f"{sfx}|||")
        
        # 按照 "|||" 强行切断
        raw_chunks = processed_zone.split("|||")
        page_channels = []
        
        for chunk in raw_chunks:
            chunk_str = chunk.strip()
            # 过滤干扰脏数据
            if chunk_str and 2 <= len(chunk_str) <= 15:
                page_channels.append(chunk_str)

        print(f"  📊 边界切片成功 -> 提取本页独立电台数: {len(page_channels)} 个")
        
        # 🪐 4. 严格保序录入字典
        for ch_name in page_channels:
            ch_clean = clean_channel_name(ch_name)
            if not ch_clean or len(ch_clean) <= 1: continue
            
            group_clean = regex_classify_channel(ch_clean, t_url)
            
            # 如果之前已经有过这个台，为了不破坏它之前的“首次排队位置”，这里不覆盖，或者你可以选择覆盖。
            # 按照从上到下的读取顺序，首次出现的位置最能体现 url 文件的真实结构。
            if ch_clean not in group_dict:
                group_dict[ch_clean] = group_clean

    # 🌟 队尾追加：规范化注入标准央视核心资产底座，老老实实排在最末尾
    for i in range(1, 18): 
        if f"CCTV-{i}" not in group_dict:
            group_dict[f"CCTV-{i}"] = "央视频道"
    if "CCTV-5+" not in group_dict:
        group_dict["CCTV-5+"] = "央视频道"

    with open(GROUP_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(group_dict, f, indent=2, ensure_ascii=False)

    save_cache_meta(cache_meta)
    duration = time.time() - start_time
    print(f"\n🎉 完美收工！大集群共计收录了 {len(group_dict)} 个无暇电台。")
    print(f"   最终的 group.json 已经严格按照你 url 文件的读取顺序进行了从上到下的完美排列！")

if __name__ == "__main__":
    main()