import requests
import re
import json
import os
import sys
import platform
from collections import defaultdict
from pypinyin import lazy_pinyin
import urllib3

# 1. 环境配置
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
sys.stdout.reconfigure(encoding='utf-8')

# 强制定位当前文件夹
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# 将所有文件都锁定在当前文件夹
RAW_TXT_PATH = os.path.join(CURRENT_DIR, "origin_raw.txt") 
OUTPUT_JSON_PATH = os.path.join(CURRENT_DIR, "name.json")

def has_chinese(text):
    return any('\u4e00' <= char <= '\u9fa5' for char in text)

def clean_alias_rules(text):
    if not text: return ""
    junk = [
        r'\.cn@SD', r'\.cn@HD', r'\.hk@SD', r'\.png$',
        r'2160p', r'1080p', r'720p', r'576p', r'576i', r'540p', r'480p', r'360p', r'180p',
        r'\[Not 24/7\]', r'\[Geo-blocked\]', r'\(.*?\)', r'（.*?）', r'\[.*?\]'
    ]
    for p in junk:
        text = re.sub(p, '', text, flags=re.I)
    return text.replace('-', '').replace('_', '').replace(' ', '').strip()

def translate_and_pinyin(text):
    text = re.sub(r'(?i)TV', '', text)
    text = re.sub(r'(?i)Satellite', '卫视', text)
    pure = text.replace(' ', '')
    return "".join(lazy_pinyin(pure)).lower()

def run_pipeline():
    headers = {'User-Agent': 'Mozilla/5.0'}
    STD_API = "https://api.github.com/repos/fanmingming/live/contents/tv"
    M3U_URL = "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u"

    try:
        # --- 第一步：抓取源码 ---
        print(f">>> [1/4] 环境: {platform.system()} | 目录: {CURRENT_DIR}")
        print(f">>> [2/4] 正在抓取源码并保存至 {os.path.basename(RAW_TXT_PATH)}...")
        
        r_m3u = requests.get(M3U_URL, headers=headers, timeout=30, verify=False)
        r_m3u.raise_for_status()
        with open(RAW_TXT_PATH, "w", encoding="utf-8") as f:
            f.write(r_m3u.text)

        # --- 第二步：获取标准库 ---
        print(">>> [3/4] 正在加载标准库 (fanmingming/live)...")
        resp = requests.get(STD_API, headers=headers, timeout=20, verify=False)
        resp.raise_for_status()
        
        std_lookup = {}
        pinyin_lookup = {}
        for item in resp.json():
            name = item.get('name', '')
            if name.endswith('.png'):
                sid = name.replace('.png', '')
                c_std = clean_alias_rules(sid).lower()
                std_lookup[c_std] = sid
                pk = "".join(lazy_pinyin(c_std.replace('台', '').replace('频道', ''))).lower()
                pinyin_lookup[pk] = sid

        # --- 第三步：解析与智能匹配 (优化版) ---
        print(">>> [4/4] 正在执行逐行解析匹配 (此步较快)...")
        aggregated = defaultdict(list)
        
        with open(RAW_TXT_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # 遍历每一行，不再使用全局 re.findall 以免卡死
        for i in range(len(lines)):
            line = lines[i].strip()
            if line.startswith("#EXTINF:"):
                # 提取 tvg-id, tvg-name 和 最后的显示名
                tid = re.search(r'tvg-id="(.*?)"', line)
                tname = re.search(r'tvg-name="(.*?)"', line)
                dname = line.split(",")[-1].strip()
                
                tid = tid.group(1) if tid else ""
                tname = tname.group(1) if tname else ""
                
                # 优先级逻辑
                candidates = [tname, tid, dname]
                best_raw = dname
                for c in candidates:
                    if has_chinese(c):
                        best_raw = c
                        break
                else:
                    best_raw = tid if tid else dname

                cl = clean_alias_rules(best_raw)
                mid = std_lookup.get(cl.lower())
                
                if not mid:
                    pk = translate_and_pinyin(cl)
                    mid = pinyin_lookup.get(pk)

                if mid and dname not in aggregated[mid]:
                    aggregated[mid].append(dname)

        # --- 第四步：保存 ---
        with open(OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump({k: ",".join(v) for k, v in aggregated.items()}, f, indent=2, ensure_ascii=False)
        
        print(f"\n✨ 处理完成！\n- 源码: {RAW_TXT_PATH}\n- 结果: {OUTPUT_JSON_PATH}")

    except Exception as e:
        print(f"\n❌ 运行失败: {str(e)}")

    input("\n按回车退出...")

if __name__ == "__main__":
    run_pipeline()