import os
import sys
import re
import json
import time
import urllib.request
from collections import OrderedDict

# 1. 严格锁定当前工作目录与所有关联文件路径（保持不变）
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
URL_FILE_PATH = os.path.join(CURRENT_DIR, "url")
GROUP_JSON_PATH = os.path.join(CURRENT_DIR, "group.json")

# 本地保存网页源码的文件夹及缓存时间戳元数据
HTML_SAVE_DIR = os.path.join(CURRENT_DIR, "html_cache")
CACHE_META_PATH = os.path.join(CURRENT_DIR, ".url_cache_meta")

CACHE_LIMIT_DAYS = 7  # 强缓存周期：7天（一星期）

def clean_channel_name(name):
    """极致清洗：去除多余噪声和残留的括号，保持频道名纯粹"""
    if not name: return ""
    name = str(name).strip()
    junk = [r'2160p', r'1080p', r'720p', r'hd', r'sd', r'超清', r'高清', r'标清']
    for pattern in junk:
        name = re.sub(pattern, '', name, flags=re.I)
    
    # 强力消灭括号：连同括号本身及内部残留内容一起整个连根拔起
    name = re.sub(r'\([^)]*\)|\[[^\]]*\]|（[^）]*）|【[^】]*】', '', name)
    
    # 清理前后残存的连接符和空格
    name = re.sub(r'^[ \-_\|\+=]+|[ \-_\|\+=]+$', '', name)
    return name.strip()

def get_target_urls():
    """从当前目录的 url 文件中读取待爬取的网址列表"""
    if not os.path.exists(URL_FILE_PATH):
        print(f"❌ 错误：在当前目录下未找到【url】文件，请先创建它。")
        return []
    urls = []
    with open(URL_FILE_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and line.startswith("http"):
                urls.append(line)
    return urls

def load_cache_meta():
    """读取本地缓存时间表"""
    if os.path.exists(CACHE_META_PATH):
        try:
            with open(CACHE_META_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_cache_meta(meta):
    """保存更新缓存时间表"""
    try:
        with open(CACHE_META_PATH, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def get_url_filename(url, index):
    """根据URL特征生成合法的本地稳固文件名"""
    clean_url = re.sub(r'[\/\\\:\*\?\"\<\>\|]', '_', url)
    if len(clean_url) > 60:
        clean_url = clean_url[-60:]
    return f"cache_{index}_{clean_url}.txt"

def fetch_m3u_with_cache(target_url, index, cache_meta):
    """💡 强缓存引擎：一星期内优先读取本地。GitHub Raw 极其稳定，用标准 urllib 直接请求"""
    local_filename = get_url_filename(target_url, index)
    local_path = os.path.join(HTML_SAVE_DIR, local_filename)
    
    last_success_time = cache_meta.get(target_url, 0)
    days_passed = (time.time() - last_success_time) / (24 * 3600)
    
    # 🎯 强缓存命中：本地文件存在且未满7天，直接复用
    if os.path.exists(local_path) and days_passed < CACHE_LIMIT_DAYS:
        print(f"    📦 [缓存命中] 该直播源在一星期内已同步过（过去 {days_passed:.1f} 天），直接读取本地文件。")
        try:
            with open(local_path, 'r', encoding='utf-8') as f:
                return f.read(), False
        except Exception:
            pass

    # 🌐 未命中缓存：直接连网获取原始 M3U 文本
    print(f"    🌐 [独立请求] 正在联网向远端同步原始 M3U 数据...")
    
    req = urllib.request.Request(target_url)
    req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
    
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            content = response.read().decode('utf-8', errors='ignore')
            
            # 请求成功，立即写入本地缓存文件夹
            with open(local_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            cache_meta[target_url] = time.time()
            return content, True
    except Exception as e:
        print(f"    ❌ 连网同步失败 ({e})")
        if os.path.exists(local_path):
            print(f"    ℹ️ [降级保底] 自动载入本地历史缓存数据进行解析。")
            try:
                with open(local_path, 'r', encoding='utf-8') as f:
                    return f.read(), False
            except Exception:
                pass
        return None, False

def main():
    print("==========================================")
    print("     Group.py 直播源智能解析与强分类")
    print("==========================================")
    
    start_time = time.time()
    target_urls = get_target_urls()
    if not target_urls:
        return
        
    if not os.path.exists(HTML_SAVE_DIR):
        os.makedirs(HTML_SAVE_DIR)
        
    cache_meta = load_cache_meta()
    group_dict = OrderedDict()
    
    network_request_count = 0

    # 开始循环解析文件里配置的 M3U 链接
    for idx, t_url in enumerate(target_urls, start=1):
        print(f" 🔍 正在处理数据源: {t_url}")
        m3u_content, is_net = fetch_m3u_with_cache(t_url, idx, cache_meta)
        if is_net: network_request_count += 1
        if not m3u_content: continue
        
        # 💡 M3U 核心学习解析算法：
        # 匹配形如: group-title="浙江频道",萧山新闻综合
        # 正则解析：group-title="([^"]+)" 用于抓取分组名，,\s*([^\r\n]+) 用于抓取逗号后面的频道名
        pattern = re.compile(r'group-title="([^"]+)".*?,\s*([^\r\n]+)', re.IGNORECASE)
        matches = pattern.findall(m3u_content)
        
        print(f"    📊 正在扫描文本... 本源成功提取到 {len(matches)} 条直播项")
        
        for group_name, ch_name in matches:
            ch_clean = clean_channel_name(ch_name)
            group_clean = group_name.strip()
            
            # 过滤噪音数据
            if not ch_clean or len(ch_clean) <= 1:
                continue
                
            # 去除组名中可能夹杂的特殊Emoji图标（保持组名纯净）
            group_clean = re.sub(r'[^\u4e00-\u9fa5\w\-]', '', group_clean).strip()
            if not group_clean:
                group_clean = "地方频道"
            
            # 智能分流归档逻辑
            if "CCTV" in ch_clean.upper() or ch_clean.startswith("中央"):
                group_dict[ch_clean] = "央视频道"
            elif "卫视" in ch_clean:
                group_dict[ch_clean] = "地方卫视"
            else:
                # 完美继承 M3U 文件里自带的分组标签（例如“浙江频道”、“🎦浙江频道”洗完后变成“浙江频道”）
                group_dict[ch_clean] = group_clean

    # 3. 兜底高优注入，确保央视基础底库不丢
    for i in range(1, 18):
        group_dict[f"CCTV{i}"] = "央视频道"
    group_dict["CCTV5+"] = "央视频道"

    # 4. 成功落地存储组装字典为 group.json
    with open(GROUP_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(group_dict, f, indent=2, ensure_ascii=False)
        
    save_cache_meta(cache_meta)
    duration = time.time() - start_time
    
    print("\n==========================================")
    print("         Group.py 强缓存机制运行报告")
    print("==========================================")
    print(f"- 本次运行状态       : [完成] 数据已同步至当前目录")
    print(f"- 本次真正连网请求数 : {network_request_count} 次 (其余均复用本地历史数据)")
    print(f"- 本次智能收录电台数 : {len(group_dict)} 个")
    print(f"- 任务总消耗时长     : {duration:.2f} 秒")
    print(f"- 成果落地方向       : {os.path.basename(GROUP_JSON_PATH)}")
    print("==========================================\n")

if __name__ == "__main__":
    main()