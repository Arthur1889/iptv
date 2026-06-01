import os
import sys
import json
import re
import time
import subprocess
import urllib.request
import urllib.error
import concurrent.futures
from collections import defaultdict
from urllib.parse import urlparse

# =====================================================================
# 0. 环境自检与依赖自动安装
# =====================================================================
def auto_check_environment():
    sys_type = sys.platform
    print(f"[*] 启动 IPTV 聚合爬虫...")
    print(f"[*] 当前系统环境检测为: {sys_type}")
    
    required_packages = ["requests"]
    for pkg in required_packages:
        try:
            __import__(pkg)
        except ImportError:
            print(f"[-] 缺少依赖 {pkg}，正在尝试自动安装...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
                print(f"[+] {pkg} 安装成功！")
            except Exception as e:
                print(f"[X] 自动安装 {pkg} 失败，请手动执行 pip install {pkg}。错误: {e}")
                sys.exit(1)

auto_check_environment()
import requests

# =====================================================================
# 1. 全局配置与路径初始化
# =====================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(BASE_DIR, "sources.json")
CACHE_PATH = os.path.join(BASE_DIR, "sources_cache.txt") # 建议运行前删除旧缓存
BLACKLIST_PATH = os.path.join(BASE_DIR, "blacklist.json")
OUTPUT_PATH = os.path.join(BASE_DIR, "tv.m3u")
LOG_PATH = os.path.join(BASE_DIR, "crawl.log")

# 引入外部依赖配置
GROUP_JSON_PATH = os.path.join(BASE_DIR, "group", "group_standard.json")
NAME_JSON_PATH = os.path.join(BASE_DIR, "iptvname", "name.json")

# 预设的央视映射与标准组序
CCTV_DESC_MAP = {
    "CCTV1": "CCTV-1 综合", "CCTV2": "CCTV-2 财经", "CCTV3": "CCTV-3 综艺",
    "CCTV4": "CCTV-4 中文国际", "CCTV5": "CCTV-5 体育", "CCTV5+": "CCTV-5+ 体育赛事",
    "CCTV6": "CCTV-6 电影", "CCTV7": "CCTV-7 国防军事", "CCTV8": "CCTV-8 电视剧",
    "CCTV9": "CCTV-9 纪录", "CCTV10": "CCTV-10 科教", "CCTV11": "CCTV-11 戏曲",
    "CCTV12": "CCTV-12 社会与法", "CCTV13": "CCTV-13 新闻", "CCTV14": "CCTV-14 少儿",
    "CCTV15": "CCTV-15 音乐", "CCTV16": "CCTV-16 奥林匹克", "CCTV17": "CCTV-17 农业农村",
    "CCTV4K": "CCTV4K 超高清", "CCTV8K": "CCTV8K 超高清"
}

GROUP_ORDER = [
    "4K频道", "央视频道", "地方卫视", "山东频道", "地方频道", "少儿频道", 
    "港澳台", "影视频道", "歌曲及音乐MV", "纪录纪实", "娱乐频道", 
    "电视剧直播", "动漫直播", "海外频道", "体育赛事", "综合频道"
]

# 地区标识用于 4K 归类兜底
PROVINCES = ["北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江", "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州", "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆"]

# =====================================================================
# 2. 核心清洗与规则处理模块
# =====================================================================
def load_json(filepath, default_val=None):
    if not os.path.exists(filepath):
        return default_val if default_val is not None else {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def build_name_lookup(name_json_data):
    """构建 别名 -> 标准名 的极速查找字典"""
    lookup = {}
    for std_name, aliases in name_json_data.items():
        lookup[std_name.upper()] = std_name
        for alias in aliases:
            lookup[alias.upper()] = std_name
    return lookup

def clean_and_normalize_name(raw_name, name_lookup):
    """执行名称清洗、标准化和要求6、8、9的处理"""
    # [要求 9] 保护 4K/8K 标识
    has_4k = "4K" in raw_name.upper()
    has_8k = "8K" in raw_name.upper()
    
    # [要求 6] 过滤杂质后缀
    noise_patterns = [
        r'360[pP]', r'404[pP]', r'480[pP]', r'576[pP]', r'606[pP]', r'720[pP]', 
        r'1080[pP]', r'1080[iI]', r'[hH][dD]', r'Not 24/7', r'Geo-blocked', 
        r'\[.*?\]', r'\(.*?\)'
    ]
    cleaned = raw_name
    for pattern in noise_patterns:
        cleaned = re.sub(pattern, '', cleaned)
    cleaned = cleaned.strip('- ').strip()

    # 标准化映射匹配
    std_name = name_lookup.get(cleaned.upper(), cleaned)
    
    # [要求 8 & 央视描述] 特殊处理 CCTV 格式
    upper_name = std_name.upper()
    if "CCTV5+" in upper_name or "CCTV5PLUS" in upper_name:
        return CCTV_DESC_MAP["CCTV5+"]
    if "CCTV4K" in upper_name: return CCTV_DESC_MAP["CCTV4K"]
    if "CCTV8K" in upper_name: return CCTV_DESC_MAP["CCTV8K"]
    
    cctv_match = re.search(r'^CCTV(\d+)$', upper_name)
    if cctv_match:
        return CCTV_DESC_MAP.get(f"CCTV{cctv_match.group(1)}", std_name)
        
    # 如果原始名字有 4K 但清洗或映射后丢失了，恢复标记
    if has_4k and "4K" not in std_name.upper(): std_name += " 4K"
    if has_8k and "8K" not in std_name.upper(): std_name += " 8K"
    
    return std_name

def determine_final_group(std_name, raw_group, group_repo):
    """绝对优先级的智能分组引擎 (匹配 group_standard.json > 4K规则 > 13条Fallback)"""
    name_up = std_name.upper()
    rg = raw_group.strip() if raw_group else ""
    is_4k_8k = "4K" in name_up or "8K" in name_up

    # [要求 3 & 分组11] 强制垃圾分类抛弃
    drop_list = ["游戏直播", "听书直播", "老年直播", "解说直播", "监控直播", "蜘蛛直播", "zuqiu直播", "咪视界直播", "KK直播", "瑜伽裤直播", "Ai直播", "钓鱼直播", "API随机点播", "直播室", "测试"]
    if any(x in rg or x in name_up for x in drop_list):
        return None

def sort_key(x):
    # 直接使用顶部的全局变量 GROUP_PRIORITY
    g_idx = GROUP_PRIORITY.index(x["group"]) if x["group"] in GROUP_PRIORITY else 999
    
    sub_idx = 99
    if x["group"] == "4K频道":
        if "CCTV" in x["std_name"]: sub_idx = 1
        elif "卫视" in x["std_name"]: sub_idx = 2
        else: sub_idx = 3
    
    cctv_num = re.search(r'CCTV-(\d+)', x["std_name"])
    cctv_idx = int(cctv_num.group(1)) if cctv_num else 999
    
    return (g_idx, sub_idx, cctv_idx, -x["resolution"])

    # 第一优先级：匹配 group_standard.json
    group_from_json = group_repo.get(std_name)
    if group_from_json:
        return group_from_json

    # 判断区域与央视属性
    is_cctv = any(x in name_up for x in ["CCTV", "中央台", "CGTN"])
    is_ws = "卫视" in name_up and "朝鲜语" not in name_up
    is_df_zone = any(x in name_up for x in PROVINCES)

    # 第二优先级：[要求 7 & 8] 4K频道特殊规则
    if "CCTV4K" in name_up or "CCTV8K" in name_up: return "4K频道"
    if is_4k_8k and (is_cctv or is_ws or is_df_zone): return "4K频道"

    # 第三优先级：按原始组别执行 13 条兜底规则
    if "地方台直播" in rg: return "地方频道"
    if "港澳台直播" in rg: return "港澳台"
    if any(x in rg for x in ["马来西亚", "越南", "印度", "日本", "韩国", "美国", "英国", "爱尔兰", "全球"]): return "海外频道"
    if "少儿直播" in rg: return "少儿频道"
    if "体育直播" in rg: return "体育赛事"
    if "电影直播" in rg: return "影视频道"
    if any(x in rg for x in ["综艺", "短剧", "小品", "相声", "抖音", "YY直播", "车模", "女团", "热舞", "乡野", "脱口秀"]): return "娱乐频道"
    if any(x in rg for x in ["电视剧直播", "爱奇艺", "埋堆堆"]): return "电视剧直播"
    if "纪录片直播" in rg: return "纪录纪实"
    if any(x in rg for x in ["动漫直播", "沙雕动画"]): return "动漫直播"
    if any(x in rg for x in ["音乐直播", "周杰伦", "歌手合集"]): return "歌曲及音乐MV"

    if any(k in std_name for k in ["电影", "影视", "影片", "影院"]):
        return "影视频道"
    if any(k in std_name for k in ["短剧", "剧场"]):
        return "电视剧直播"

    # 最终智能兜底
    if is_cctv: return "央视频道"
    if is_ws: return "地方卫视"
    if is_df_zone: return "地方频道"
    return "综合频道"

# =====================================================================
# 3. 探测、去重与输出控制
# =====================================================================
def probe_url(url):
    """探测 URL，返回 (是否有效, 解析度高)"""
    try:
        # 为了探测速度，设置 3 秒超时
        resp = requests.get(url, timeout=3, stream=True)
        if resp.status_code != 200:
            return False, 0
            
        # 尝试简单解析分辨率
        resolution = 1080 # 默认给一个合格分数，防止误杀非m3u8格式的优质源
        content_type = resp.headers.get("Content-Type", "")
        if "mpegurl" in content_type or "m3u8" in url:
            # 只取前 1024 字节判断，避免卡死
            chunk = next(resp.iter_content(chunk_size=1024)).decode('utf-8', errors='ignore')
            res_match = re.search(r'RESOLUTION=\d+x(\d+)', chunk)
            if res_match:
                resolution = int(res_match.group(1))
        return True, resolution
    except:
        return False, 0

def process_and_deduplicate(channels):
    """[要求 2, 4, 5] 核心归类去重与排序管道"""
    channel_groups = defaultdict(list)
    for ch in channels:
        channel_groups[ch["std_name"]].append(ch)
        
    final_retained = []
    
    for std_name, info_list in channel_groups.items():
        # 按分辨率和探测顺位排序
        info_list.sort(key=lambda x: x["resolution"], reverse=True)
        
        is_cctv_or_ws = "CCTV" in std_name or "卫视" in std_name
        max_res = info_list[0]["resolution"]
        
        valid_items = []
        for item in info_list:
            if item["resolution"] >= 720:
                valid_items.append(item)
            elif is_cctv_or_ws and max_res < 720:
                valid_items.append(item)
                break # 央视/卫视低清兜底只留1个
                
        if not valid_items: continue
            
        # [要求 4] 画质分流双通道去重
        high_4k = None
        standard = None
        for item in valid_items:
            if item["resolution"] >= 2160:
                if not high_4k: high_4k = item
            else:
                if not standard: standard = item
                
        if high_4k: final_retained.append(high_4k)
        if standard: final_retained.append(standard)

    final_retained.sort(key=sort_key)
    return final_retained

# =====================================================================
# 4. 主干运行流程
# =====================================================================
def main():
    start_time = time.time()
    
    # 1. 加载配置字典
    group_repo = load_json(GROUP_JSON_PATH, {})
    name_repo = load_json(NAME_JSON_PATH, {})
    blacklist = load_json(BLACKLIST_PATH, {})
    name_lookup = build_name_lookup(name_repo)
    
    # 模拟从缓存/源提取 (这里简化模拟已合并的原始行列表，实际根据你的 sources 提取机制对接)
    raw_m3u_lines = []
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            raw_m3u_lines = f.readlines()
    else:
        print("[-] 未找到源缓存文件 sources_cache.txt，请确保第一步成功运行！")
        return

    # 数据结构梳理
    parsed_items = []
    for i in range(len(raw_m3u_lines)):
        line = raw_m3u_lines[i].strip()
        if line.startswith("#EXTINF"):
            # 基础信息正则解析
            name_match = re.search(r',(.*)$', line)
            if not name_match: continue
            raw_name = name_match.group(1).strip()
            
            # 过滤 catvod [要求 3]
            url = raw_m3u_lines[i+1].strip() if i+1 < len(raw_m3u_lines) else ""
            if "catvod.com" in url or not url.startswith("http"): continue
            
            # 解析附加信息 [要求 12]
            logo = re.search(r'tvg-logo="(.*?)"', line)
            logo = logo.group(1) if logo else ""
            grp = re.search(r'group-title="(.*?)"', line)
            grp = grp.group(1) if grp else ""
            tvgid = re.search(r'tvg-id="(.*?)"', line)
            tvgid = tvgid.group(1) if tvgid else ""
            
            parsed_items.append({
                "raw_name": raw_name, "url": url, "logo": logo, 
                "group": grp, "tvgid": tvgid
            })

    total_sources = len(parsed_items)
    stats = {
        "initial_total": total_sources,
        "blacklist_filtered": 0,
        "quality_filtered": 0,
        "final_retained": 0
    }
    
    valid_channels = []
    
    print(f"\n[+] 准备探测 {total_sources} 个源 (已开启多线程加速)...")
    
    # 1. 先把需要探测的有效任务筛选出来，避免在多线程里做无用功
    tasks = []
    for item in parsed_items:
        std_name = clean_and_normalize_name(item["raw_name"], name_lookup)
        final_group = determine_final_group(std_name, item["group"], group_repo)
        
        if not final_group:
            stats["quality_filtered"] += 1
            continue
            
        url = item["url"]
        try:
            fails = int(blacklist.get(url, 0))
        except (ValueError, TypeError):
            fails = 0
            
        if fails >= 3:
            stats["blacklist_filtered"] += 1
            continue
            
        tasks.append({
            "std_name": std_name, "url": url, "logo": item["logo"], 
            "group": final_group
        })

    # 2. 定义单个探测任务的包装函数
    def check_task(task):
        is_valid, res = probe_url(task["url"])
        return task, is_valid, res

    total_tasks = len(tasks)
    completed = 0
    
    # 3. 开启多线程并发探测 (max_workers=20 表示同时测 20 个源，可根据网络情况微调)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        # 提交所有任务
        future_to_url = {executor.submit(check_task, task): task for task in tasks}
        
        # 只要有任务完成就立刻处理结果
        for future in concurrent.futures.as_completed(future_to_url):
            completed += 1
            task, is_valid, res = future.result()
            url = task["url"]
            
            # 单行进度刷新
            print(f"\r进度: {completed}/{total_tasks} | 正在测: {task['std_name'][:10]:<10} | 优质源: {len(valid_channels)}", end="", flush=True)

            if is_valid:
                if url in blacklist: del blacklist[url]
                valid_channels.append({
                    "std_name": task["std_name"],
                    "url": url,
                    "logo": task["logo"],
                    "tvgid": task["std_name"],
                    "group": task["group"],
                    "resolution": res
                })
            else:
                try:
                    fails = int(blacklist.get(url, 0))
                except (ValueError, TypeError):
                    fails = 0
                blacklist[url] = fails + 1

    print("\n[+] 探测完毕，正在执行深度去重与排序...")
    
    # 4. 后置聚合与生成
    final_list = process_and_deduplicate(valid_channels, GROUP_PRIORITY)
    stats["final_retained"] = len(final_list)
    stats["quality_filtered"] += (len(valid_channels) - len(final_list))

    # [要求 10 & 13] 生成最终文件
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://epg.112114.xyz/"\n')
        for ch in final_list:
            f.write(f'#EXTINF:-1 tvg-id="{ch["std_name"]}" tvg-name="{ch["std_name"]}" tvg-logo="{ch["logo"]}" group-title="{ch["group"]}",{ch["std_name"]}\n')
            f.write(f'{ch["url"]}\n')

    # 保存黑名单
    save_json(BLACKLIST_PATH, blacklist)

    # [要求 1] 打印并保存任务终期报告
    elapsed = time.time() - start_time
    report = f"""
================ 📊 IPTV 任务运行报告 ================
[+] 运行时间: {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())}
[+] 初始读取源总数: {stats['initial_total']}
[+] 命中黑名单拦截: {stats['blacklist_filtered']}
[+] 画质/垃圾源过滤: {stats['quality_filtered']}
[+] 最终保留优质源: {stats['final_retained']}
[+] 脚本整体总耗时: {elapsed:.2f} 秒
======================================================
"""
    print(report)
    with open(LOG_PATH, "a", encoding="utf-8") as log_f:
        log_f.write(report)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] 任务被手动中断。")