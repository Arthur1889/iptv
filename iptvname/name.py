import requests
import re
import json
import os
import sys
from collections import defaultdict
from pypinyin import lazy_pinyin
import urllib3

# 禁用 SSL 警告（防止代理抓包干扰）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
sys.stdout.reconfigure(encoding='utf-8')
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

def has_chinese(text):
    return any('\u4e00' <= char <= '\u9fa5' for char in text)

def clean_alias_rules(text):
    """规则2：第一轮清洗"""
    if not text: return ""
    junk = [
        r'\.cn@SD', r'\.cn@HD', r'\.hk@SD', r'\.png$',
        r'2160p', r'1080p', r'720p', r'576p', r'576i', r'540p', r'480p', r'360p', r'180p',
        r'\[Not 24/7\]', r'\[Geo-blocked\]', r'\(.*?\)', r'（.*?）', r'\[.*?\]'
    ]
    for p in junk:
        text = re.sub(p, '', text, flags=re.I)
    # 去掉末尾空格和特定符号
    return text.replace('-', '').replace('_', '').strip()

def translate_and_pinyin(text):
    """规则3：第二轮翻译与拼音"""
    # 删掉 TV，将 Satellite 翻译为 卫视
    text = re.sub(r'(?i)TV', '', text)
    text = re.sub(r'(?i)Satellite', '卫视', text)
    pure = text.replace(' ', '')
    return "".join(lazy_pinyin(pure)).lower()

def run_pipeline():
    headers = {'User-Agent': 'Mozilla/5.0'}
    # 网址与 crawl 逻辑一致
    STD_API = "https://api.github.com/repos/fanmingming/live/contents/tv"
    M3U_URL = "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u"

    try:
        # 1. 加载标准库 (fanmingming)
        print(">>> [1/4] 正在获取标准库...", flush=True)
        # 不传 proxies 参数，让 requests 自动寻找系统/Git 代理
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
                # 预生成拼音索引
                pk = "".join(lazy_pinyin(c_std.replace('台', '').replace('频道', ''))).lower()
                pinyin_lookup[pk] = sid
        print(f"✅ 标准库加载成功: {len(std_lookup)} 个频道")

        # 2. 获取原始别名源 (iptv-org)
        print("\n>>> [2/4] 正在抓取别名源源码...", flush=True)
        r = requests.get(M3U_URL, headers=headers, timeout=30, verify=False)
        r.raise_for_status()
        m3u_text = r.text
        
        matches = re.findall(r'#EXTINF:-1.*?tvg-id="(.*?)".*?tvg-name="(.*?)".*?,(.*?)\n', m3u_text, re.S)
        total = len(matches)
        print(f"✅ 抓取成功，共有 {total} 个待处理频道")

        # 3. 智能匹配聚合
        print("\n>>> [3/4] 正在执行规则匹配...", flush=True)
        aggregated = defaultdict(list)
        for i, (tid, tname, dname) in enumerate(matches):
            if i % 200 == 0 or i == total - 1:
                sys.stdout.write(f"\r进度: [{i+1}/{total}]")
                sys.stdout.flush()

            raw_alias = dname.strip()
            # 规则1优先级：含中文 > tid > dname
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

            if mid:
                if raw_alias not in aggregated[mid]:
                    aggregated[mid].append(raw_alias)

        # 4. 保存
        output_path = os.path.join(CURRENT_DIR, 'name.json')
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump({k: ",".join(v) for k, v in aggregated.items()}, f, indent=2, ensure_ascii=False)
        
        print(f"\n\n✨ 处理完成！结果已存至: {output_path}")

    except Exception as e:
        print(f"\n❌ 连接失败: {str(e)}")
        print("\n💡 建议：既然 crawl 能跑通，请确保在同一个终端窗口运行此脚本。")

    input("\n按回车退出...")

if __name__ == "__main__":
    run_pipeline()