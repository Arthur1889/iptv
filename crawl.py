import os
import platform
import subprocess
import sys
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 0. 环境准备 =================
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def ensure_dependencies():
    required_libs = ["requests", "tqdm"]
    for lib in required_libs:
        try:
            __import__(lib)
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", lib])

ensure_dependencies()
import requests
from tqdm import tqdm

def get_env_config():
    sys_type = platform.system()
    config = {
        "os": sys_type, 
        "ffprobe": "ffprobe", 
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", 
        "timeout": 12, 
        "workers": 50 
    }
    if sys_type == "Windows" and os.path.exists(r"C:\ffmpeg\bin\ffprobe.exe"):
        config["ffprobe"] = r"C:\ffmpeg\bin\ffprobe.exe"
    return config

ENV = get_env_config()

# ================= 1. 加载配置与别名表 =================
CONFIG_FILE = "sources.json"
NAME_JSON = "name.json"

def load_sources():
    if not os.path.exists(CONFIG_FILE): return []
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f).get("urls", [])
    except: return []

def load_alias_map():
    alias_dict = {}
    if not os.path.exists(NAME_JSON): return alias_dict
    try:
        with open(NAME_JSON, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith('#')]
            for line in lines:
                parts = line.split(',')
                if len(parts) < 2: continue
                main_name = parts[0].strip()
                aliases = parts[1:]
                alias_dict[main_name] = aliases
    except Exception as e:
        print(f"⚠️ 加载 name.json 失败: {e}")
    return alias_dict

SOURCE_URLS = load_sources()
ALIAS_MAP = load_alias_map()

# 增加山东频道优先级
GROUP_PRIORITY = {"央视频道": 1, "地方卫视": 2, "山东频道": 3, "上海频道": 4, "港澳台": 5, "电影/影院": 6, "体育/竞技": 7, "英文/国际": 8, "纪录/纪实": 9, "少儿/动画": 10}

# ================= 2. 核心逻辑函数 =================

def get_standard_name(origin_name):
    # 增加大小写和前后空格的容错
    name_upper = origin_name.strip().upper()
    for main_name, aliases in ALIAS_MAP.items():
        for alias in aliases:
            alias = alias.strip()
            if alias.startswith("re:"):
                pattern = alias[3:]
                try:
                    if re.search(pattern, origin_name, re.I): return main_name
                except: continue
            elif alias.upper() in name_upper or name_upper in alias.upper():
                return main_name
    return origin_name

def clean_channel_name(name, height=0, original_name=""):
    """
    清洗名称并根据物理像素打标
    整合优化点 1：彻底规避 xx卫视 与 xx卫视HD 的重复。
    整合优化点 2：修复 4K 频道没有节目单的问题。
    """
    # 1. 物理清洗：剔除高清、HD、超高清、蓝光等无关后缀（这是解决卫视重复的关键）
    cleaned_origin = re.sub(r'(HD|高清|超高清|蓝光|频道|\(备用\))', '', original_name if original_name else name, flags=re.I).strip()
    
    # 2. 首先获取映射后的标准主名
    base_name = get_standard_name(cleaned_origin)
    
    # 3. 彻底清除主名结尾可能存在的旧画质标签（解决重复堆叠Bug）
    base_name = re.sub(r'(-4K|-8K|4K|8K|超高清)$', '', base_name, flags=re.I).strip()

    # 4. 物理与文字识别 (4K/8K)
    is_8k = height >= 4320 or re.search(r'8K', original_name, re.I)
    is_4k = height >= 2160 or re.search(r'4K', original_name, re.I)

    # 5. 重新打标
    final_name = base_name
    is_ultra = False
    if is_8k: 
        final_name = f"{base_name}-8K"
        is_ultra = True
    elif is_4k: 
        final_name = f"{base_name}-4K"
        is_ultra = True
        
    # 返回： final_name(xx卫视-4K), epg_id(xx卫视), is_ultra(True)
    return final_name, base_name, is_ultra

def deep_analyze_stream(url):
    # 针对 8K 源调优参数 (增加 probesize)
    cmd = [ENV["ffprobe"], '-v', 'error', '-probesize', '4096000', '-analyzeduration', '4000000', '-user_agent', ENV["ua"], '-show_entries', 'stream=width,height,bit_rate', '-of', 'json', '-select_streams', 'v:0', '-timeout', '10000000', url]
    try:
        cf = subprocess.CREATE_NO_WINDOW if ENV["os"] == "Windows" else 0
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=ENV["timeout"], creationflags=cf)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if 'streams' in data and data['streams']:
                s = data['streams'][0]
                return int(s.get('height', 0)), int(s.get('bit_rate', 0)) or 0
    except: pass
    return 0, 0

def check_channel(ch):
    try:
        h, br = deep_analyze_stream(ch['url'])
        if h >= 360:
            ch['height'], ch['bitrate'] = h, br
            # 这里接收并赋值修复后的频道名和EPG ID
            ch['name'], ch['epg_id'], ch['is_ultra'] = clean_channel_name(ch['name'], height=h, original_name=ch['origin_name'])
            return ch, True
        else:
            res = requests.head(ch['url'], timeout=3, headers={"User-Agent": ENV["ua"]}, verify=False)
            if res.status_code == 200:
                ch['height'], ch['bitrate'] = 0, 0
                ch['name'], ch['epg_id'], ch['is_ultra'] = clean_channel_name(ch['name'], height=0, original_name=ch['origin_name'])
                if ch['is_ultra']: return ch, True
    except: pass
    return ch, False

def get_group(name):
    """
    整合优化点 3：频道组中增加山东频道分组。
    """
    n = name.upper()
    if "CCTV" in n: return "央视频道"
    if "卫视" in n: return "地方卫视"
    
    # 优先匹配山东频道
    if any(s in n for s in ["山东", "齐鲁", "济南", "青岛", "潍坊", "烟台", "淄博"]): return "山东频道"
    
    if any(s in n for s in ["上海", "东方", "新闻综合", "纪实人文"]): return "上海频道"
    
    k_map = {"港澳台": ["翡翠", "TVB", "凤凰", "明珠", "J2", "HK", "澳门", "台湾"], "电影/影院": ["电影", "影院", "CHC"]}
    for group, keys in k_map.items():
        if any(k in n for k in keys): return group
    return "综合/其他"

def sort_key(ch):
    # 排序：1.频道组 2.CCTV序号 3.画质(4K在前) 4.物理质量
    # 排序时使用 epg_id，确保 CCTV-1 和 CCTV-1-4K 在同一个台号下
    group_p = GROUP_PRIORITY.get(get_group(ch['name']), 999)
    name = ch['name']
    cctv_match = re.search(r'CCTV-(\d+)', name)
    cctv_num = int(cctv_match.group(1)) if cctv_match else 999
    
    quality_rank = 0
    if "-8K" in name: quality_rank = 20
    elif "-4K" in name: quality_rank = 10
    
    # 优化物理评分算法
    phys_score = ch.get('height', 0) * 1000000 + ch.get('bitrate', 0)
    return (group_p, cctv_num, ch['epg_id'], -quality_rank, -phys_score)

# ================= 3. 主程序 =================

def run():
    if not SOURCE_URLS: return
    all_channels = []
    seen_urls = set()
    session = requests.Session()

    print(f"\n📡 [1/3] 正在从 {len(SOURCE_URLS)} 个源提取链接...")
    for url in SOURCE_URLS:
        try:
            r = session.get(url, timeout=ENV["timeout"], verify=False)
            # 改进正则：捕获 tvg-id 和 逗号后的显示名，优先用 ID
            matches = re.findall(r'#EXTINF:.*?(?:tvg-id="(.*?)")?.*?,(.*?)\n(http.*?)(?:\n|$)', r.text)
            for tid, name, link in matches:
                link = link.strip()
                if link not in seen_urls:
                    # 优先利用 tvg-id (tid) 作为标准化依据，这最管用
                    all_channels.append({"name": tid if tid else name, "origin_name": name, "url": link})
                    seen_urls.add(link)
        except: continue

    print(f"🚀 [2/3] 提取链接: {len(all_channels)} 条 | 开启 一频道双源 择优...")
    
    # 使用 Python 字典的元组 Key 天然实现你要求的去重逻辑：
    # (频道标准ID, 是否超清) 作为 Key，同一个坑只能留最高分
    # 例如： (CCTV-1, False) 和 (CCTV-1, True) 会并存
    best_sources = {} # {(base_name, is_ultra): channel_data}
    
    valid_count = 0
    with ThreadPoolExecutor(max_workers=ENV["workers"]) as executor:
        futures = {executor.submit(check_channel, ch): ch for ch in all_channels}
        pbar_fmt = '{l_bar}{bar:20}{r_bar} {n_fmt}/{total_fmt} [{percentage:3.0f}%] 优质源:{postfix}'
        with tqdm(total=len(all_channels), desc="探测进度", bar_format=pbar_fmt) as pbar:
            for f in as_completed(futures):
                res_ch, is_ok = f.result()
                if is_ok:
                    valid_count += 1
                    pbar.set_postfix_str(str(valid_count))
                    
                    # 关键修改：使用 (标准台名, 是否超清) 作为唯一 Key
                    unique_key = (res_ch['epg_id'], res_ch['is_ultra'])
                    
                    # 同一 Key 下进行物理分 PK，score 越高物理质量越好
                    phys_score = res_ch['height'] * 1000000 + res_ch['bitrate']

                    if unique_key not in best_sources or phys_score > best_sources[unique_key]['phys_score']:
                        res_ch['phys_score'] = phys_score
                        best_sources[unique_key] = res_ch
                pbar.update(1)

    # 排序并生成 final_list
    final_list = sorted(best_sources.values(), key=sort_key)

    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://live.fanmingming.com/e.xml"\n')
        for ch in final_list:
            # 整合点 2 的具体实现：
            # tvg-id 和 logo ID 必须是标准 ID (xx卫视)，不能带 -4K，否则没节目单
            logo_id = ch['epg_id'].replace('-', '')
            group = get_group(ch['name'])
            
            f.write(f'#EXTINF:-1 tvg-id="{ch["epg_id"]}" tvg-logo="https://live.fanmingming.com/tv/{logo_id}.png" group-title="{group}",{ch["name"]}\n{ch["url"]}\n')

    print(f"\n✅ 完成！最终入选 {len(final_list)} 个优质频道。")

if __name__ == "__main__":
    start_time = time.time()
    run()
    print(f"⏱️ 全程耗时: {int(time.time() - start_time)} 秒")