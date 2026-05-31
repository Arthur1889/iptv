import os
import sys
import json
import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor

# ================= 配置 =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_JSON = os.path.join(BASE_DIR, 'sources.json')
CACHE_FILE = os.path.join(BASE_DIR, 'sources_cache.txt')
NAME_JSON = os.path.join(BASE_DIR, 'iptvname', 'name.json')
GROUP_JSON = os.path.join(BASE_DIR, 'group.json')
BLACKLIST_JSON = os.path.join(BASE_DIR, 'blacklist.json')
OUTPUT_M3U = os.path.join(BASE_DIR, 'tv.m3u')
LOG_FILE = os.path.join(BASE_DIR, 'iptv_task.log')

# 初始化日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', 
                    handlers=[logging.FileHandler(LOG_FILE, 'w', 'utf-8'), logging.StreamHandler()])

# ================= 规则引擎 =================

def clean_channel_name(name):
    """规则 6: 去除无效字眼"""
    patterns = [r'360P', r'404P', r'480P', r'576P', r'606P', r'720P', r'1080P', 
                r'HD', r'Not 24/7', r'Geo-blocked']
    for p in patterns:
        name = re.sub(p, '', name, flags=re.IGNORECASE)
    return name.strip()

def assign_group(std_name, original_group, groups_db):
    """14 条分组规则引擎"""
    # 规则 1: 优先匹配 group.json
    for group, channels in groups_db.items():
        if std_name in [c.strip() for c in channels.split(',')]:
            return group
            
    # 规则 7/8: 强制 4K/8K 归类
    if "4K" in std_name or "8K" in std_name: return "4K频道"
    
    # 规则 1-13 映射
    mapping = {
        "🌐地方台直播": "地方频道", "🔮港澳台直播": "港澳台",
        "👶🏻少儿直播": "少儿频道", "🎞️电影直播": "影视频道",
        "💽纪录片直播": "纪录纪实", "📺电视剧直播": "电视剧直播",
        "🎧音乐直播": "歌曲及音乐MV", "🎎动漫直播": "动漫直播"
    }
    for k, v in mapping.items():
        if k in original_group: return v
        
    # 规则 11: 过滤清单
    drop_list = ["🎮", "📚", "👴🏻", "🔊", "👁️‍🗨️", "👠", "🧘🏻‍♀️", "🤖", "🎣", "🎰"]
    if any(d in original_group for d in drop_list): return "DROP"
    
    return "综合频道" # 规则 14

# ================= 核心工作流 =================

def main():
    start_time = time.time()
    stats = {"total": 0, "bl": 0, "quality": 0, "valid": 0}
    
    # 加载 JSON
    name_db = json.load(open(NAME_JSON, 'r', encoding='utf-8'))
    group_db = json.load(open(GROUP_JSON, 'r', encoding='utf-8'))
    
    # [此处嵌入你原有的抓取与去重逻辑]
    
    # 规则 5: 排序权重
    GROUP_ORDER = ["4K频道", "央视频道", "地方卫视", "山东频道", "地方频道", 
                   "影视频道", "歌曲及音乐MV", "纪录纪实", "娱乐频道", 
                   "电视剧直播", "动漫直播", "港澳台", "海外频道", 
                   "体育赛事", "少儿频道", "综合频道"]

    def sort_key(item):
        g_idx = GROUP_ORDER.index(item['group']) if item['group'] in GROUP_ORDER else 99
        cctv_match = re.search(r'CCTV-(\d+)', item.get('tvg_id', ''))
        cctv_idx = int(cctv_match.group(1)) if cctv_match else 99
        return (g_idx, cctv_idx, item['name'])

    # [此处嵌入探测逻辑，规则 1: 单行进度条]
    # 使用: sys.stdout.write(f"\r探测中: {proc}/{total} | 优质: {stats['valid']}")
    
    # [生成 tv.m3u]
    # 规则 12, 13: 注入 EPG 源
    # 规则 9: 4K/8K 名称不重复修改
    
    # 规则 1: 任务报告
    duration = time.time() - start_time
    report = (f"\n=== 任务完成 ===\n"
              f"初始源梳理: {stats['total']}\n"
              f"黑名单过滤: {stats['bl']}\n"
              f"画质过滤: {stats['quality']}\n"
              f"最终有效: {stats['valid']}\n"
              f"总耗时: {duration:.2f}s")
    logging.info(report)

if __name__ == "__main__":
    main()