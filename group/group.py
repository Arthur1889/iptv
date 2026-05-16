import os
import sys
import re
import json
import time
import urllib.request
from collections import OrderedDict

# 1. 严格锁定当前工作目录与所有关联文件路径
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
URL_FILE_PATH = os.path.join(CURRENT_DIR, "url")
GROUP_JSON_PATH = os.path.join(CURRENT_DIR, "group.json")
CACHE_META_PATH = os.path.join(CURRENT_DIR, ".group_cache_meta")  # 缓存元数据文件

CACHE_LIMIT_DAYS = 7  # 缓存有效期：7天

def clean_channel_name(name):
    """极致清洗：去除多余噪声和残留的括号，保持频道名纯粹"""
    if not name: return ""
    name = str(name).strip()
    junk = [r'2160p', r'1080p', r'720p', r'hd', r'sd', r'超清', r'高清', r'[\(\（\[\【].*?[\)\）\]\】]']
    for pattern in junk:
        name = re.sub(pattern, '', name, flags=re.I)
    return name.strip()

def get_target_urls():
    """从当前目录的 url 文件中读取待爬取的网址列表"""
    if not os.path.exists(URL_FILE_PATH):
        backup_path = URL_FILE_PATH + ".txt"
        if os.path.exists(backup_path):
            return read_url_file(backup_path)
        print(f"❌ 错误：在当前目录下未找到【url】文件，请先创建它。")
        return []
    return read_url_file(URL_FILE_PATH)

def read_url_file(path):
    urls = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and line.startswith("http"):
                urls.append(line)
    return urls

def is_cache_valid():
    """检查缓存是否有效（是否存在且未满 7 天）"""
    if not os.path.exists(GROUP_JSON_PATH) or not os.path.exists(CACHE_META_PATH):
        return False
    try:
        with open(CACHE_META_PATH, 'r', encoding='utf-8') as f:
            meta = json.load(f)
            last_time = meta.get("last_success_time", 0)
            # 计算时间差（秒转化为天）
            days_passed = (time.time() - last_time) / (24 * 3600)
            if days_passed < CACHE_LIMIT_DAYS:
                print(f"ℹ️ 缓存命中：距离上一次爬取仅过去 {days_passed:.1f} 天（未满 {CACHE_LIMIT_DAYS} 天）。")
                return True
    except Exception:
        return False  # 损坏则视作失效
    return False

def main():
    print("==========================================")
    print("     Group.py 地方频道分组自动化构建")
    print("==========================================")
    
    start_time = time.time()
    
    # 💡 核心需求 4：建立爬取缓存机制，一周内则直接复用
    if is_cache_valid():
        try:
            with open(GROUP_JSON_PATH, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
            print(f"📦 已直接载入本地现有的分组底库。")
            print("==========================================")
            print("         Group.py 自动化运行报告")
            print("==========================================")
            print(f"- 本次运行状态 : [跳过] 复用本地缓存")
            print(f"- 现有电台总数 : {len(existing_data)} 个")
            print("==========================================\n")
            return
        except Exception:
            print("⚠️ 读取本地旧 JSON 失败，强制触发重新爬取...")

    # 读取目标网址
    target_urls = get_target_urls()
    if not target_urls:
        return
        
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    group_dict = OrderedDict()

    # 循环遍历 url 文件中的所有网址
    for target_url in target_urls:
        print(f">>> 正在请求目标地址: {target_url} ...")
        try:
            req = urllib.request.Request(target_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode('utf-8', errors='ignore')
        except Exception as e:
            print(f"⚠️ 网页请求失败，跳过该源: {e}")
            continue

        # 区域块正则解析
        sections = re.findall(r'(?:>|class=")([^"<>\s]{2,5}(?:省|市|地区|频道))["<>]*([\s\S]*?)(?=<div|class="|$)', html)
        
        if not sections:
            # 降级流解析
            blocks = re.findall(r'([\u4e00-\u9fa5]{2,4}(?:卫视|频道|地方))', html)
            for item in set(blocks):
                cleaned = clean_channel_name(item)
                if cleaned and len(cleaned) > 1:
                    if "卫视" in cleaned: group_dict[cleaned] = "地方卫视"
                    elif "CCTV" in cleaned.upper(): group_dict[cleaned] = "央视频道"
        else:
            # 标准块映射
            for region_name, block_content in sections:
                region_name = region_name.strip()
                if any(x in region_name for x in ["首页", "导航", "链接", "关于", "could"]):
                    continue
                    
                group_title = region_name if "频道" in region_name else f"{region_name}频道"
                channels = re.findall(r'>([\u4e00-\u9fa5\w\-\+]{2,15})<', block_content)
                
                for ch in channels:
                    ch_clean = clean_channel_name(ch)
                    if not ch_clean or len(ch_clean) <= 1 or ch_clean.startswith("http"):
                        continue
                    
                    if "CCTV" in ch_clean.upper():
                        group_dict[ch_clean] = "央视频道"
                    elif "卫视" in ch_clean:
                        group_dict[ch_clean] = "地方卫视"
                    else:
                        group_dict[ch_clean] = group_title

    # 兜底注入标准央视映射
    for i in range(1, 18):
        cctv_key = f"CCTV{i}"
        if cctv_key not in group_dict:
            group_dict[cctv_key] = "央视频道"
    if "CCTV5+" not in group_dict: group_dict["CCTV5+"] = "央视频道"

    # 将最终成果安全写入当前目录的 group.json
    with open(GROUP_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(group_dict, f, indent=2, ensure_ascii=False)
        
    # 💡 写入/更新缓存元数据文件，锁定当前成功时间戳
    try:
        with open(CACHE_META_PATH, 'w', encoding='utf-8') as f:
            json.dump({"last_success_time": time.time()}, f)
    except Exception:
        pass

    duration = time.time() - start_time
    
    # 💡 核心需求 3：生成运行报告，展现收录成果
    print("==========================================")
    print("         Group.py 自动化运行报告")
    print("==========================================")
    print(f"- 本次运行状态 : [成功] 远程网络爬取更新完成")
    print(f"- 本次收录地方电台总数 : {len(group_dict)} 个")
    print(f"- 任务总消耗时长       : {duration:.2f} 秒")
    print(f"- 成果落地方向         : {os.path.basename(GROUP_JSON_PATH)}")
    print("==========================================\n")

if __name__ == "__main__":
    main()
