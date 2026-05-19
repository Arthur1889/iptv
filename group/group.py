import os
import sys
import re
import json
import time
import webbrowser
# 🐍 使用 Python 自带的 tkinter 组件，无需额外 pip install 安装任何第三方库
from tkinter import Tk
from collections import OrderedDict

# ==================== 📌 要求 1：严格锁定当前工作目录 ====================
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

URL_FILE_PATH = os.path.join(CURRENT_DIR, "url")
GROUP_JSON_PATH = os.path.join(CURRENT_DIR, "group.json")

# ==================== 🌲 要求 5：标准省份/垂直分组名单 ====================
PROVINCES = [
    "北京", "上海", "天津", "重庆", "广东", "山东", "浙江", "江苏", "安徽", "福建", 
    "江西", "湖北", "河南", "河北", "山西", "吉林", "辽宁", "广西", "四川", "贵州", 
    "云南", "陕西", "甘肃", "青海", "宁夏", "新疆", "海南", "西藏", "黑龙江", "内蒙古"
]

BLACK_LIST = ["路", "大道", "高速", "监控", "平台", "测速", "交警", "收费站", "摄像头", "TEST", "测试"]

def clean_channel_name(name):
    if not name: return ""
    name = str(name).strip()
    junk = [r'直播', r'在线直播', r'高清', r'超清', r'频道', r'电视台', r'节目表']
    for pattern in junk:
        name = re.sub(pattern, '', name, flags=re.I)
    return name.strip()

def regex_classify_channel(clean_name, url_hint):
    name = clean_name.upper()
    url_hint = url_hint.lower()

    if any(kw in name for kw in BLACK_LIST): return "KILL_TRASH"
    if 'CCTV' in name or '中央' in name or 'CETV' in name or '教育' in name or "cctv" in url_hint: return "央视频道"
    if re.search(r'翡翠|明珠|凤凰|本港|TVB|HBO|CNBC|CNN|BBC|DISCOVERY|FOX|中天|三立|纬来|台视|无线|HOY|港|澳|台', name) or "gangaotai" in url_hint: return "港澳台"
    if '少儿' in name or '卡通' in name or '动漫' in name or '动画' in name or "shaoer" in url_hint: return "少儿频道"
    if '体育' in name or '足球' in name or '篮球' in name or '赛事' in name or "tiyu" in url_hint: return "体育频道"
    if '卫视' in name: return "卫视频道"

    for prov in PROVINCES:
        if prov in clean_name or prov.lower() in url_hint: return f"{prov}频道"

    return "地方频道"

def get_target_urls():
    if not os.path.exists(URL_FILE_PATH):
        print(f"❌ 错误：当前目录下未找到【url】文件")
        return []
    urls = []
    with open(URL_FILE_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            if line.startswith("http"):
                clean_line = re.sub(r'[；;，,\s\t\n\r]+$', '', line)
                urls.append(clean_line)
    return urls

def get_clipboard_text():
    """🛡️ 安全读取系统剪贴板文本"""
    try:
        root = Tk()
        root.withdraw()
        text = root.clipboard_get()
        root.destroy()
        return text
    except Exception:
        return ""

def main():
    print("==========================================")
    print(" 🚀 剪贴板联动·零网络请求高保真分拣引擎启动")
    print("==========================================")
    start_time = time.time()
    target_urls = get_target_urls()
    if not target_urls: return
    
    group_dict = OrderedDict()
    total_count = len(target_urls)

    # 🌟 针对复制出来的纯文本，提取连续的中文/英文/数字/符号作为电视台名字
    text_channel_pattern = re.compile(r'([\u4e00-\u9fa5\w\-\+]+(?:卫视|台|频道)?)')

    for idx, t_url in enumerate(target_urls, start=1):
        print(f"\n🎬 ===================== [ 进度: {idx} / {total_count} ] =====================")
        print(f"  ℹ️  正在自动调出网页: {t_url}")
        
        # 唤醒浏览器
        webbrowser.open(t_url, new=2)
        
        print(f"  💡 [请操作] 在浏览器网页里：按 【Ctrl+A】全选 -> 【Ctrl+C】复制")
        input(f"  👉 完成复制后，请回到这里【敲击回车键(Enter)】进行智能分拣...")
        
        # 100% 绿色无网络请求，直接摘取剪贴板
        clipboard_content = get_clipboard_text()
        if not clipboard_content:
            print("  ⚠️  警告：剪贴板里好像没有复制到任何文本，跳过此页。")
            continue

        # 匹配文本中所有的电视台名字
        raw_names = text_channel_pattern.findall(clipboard_content)
        
        page_channels = []
        for name in raw_names:
            name_strip = name.strip()
            if not name_strip or len(name_strip) <= 1: continue
            if name_strip in ["首页", "卫视频道", "中央电视台", "港澳台", "少儿频道", "体育频道", "广播电台", "节目预告", "电视台", "提交", "首 页"]:
                continue
            if re.search(r'免责声明|版权|COPYRIGHT|HOT|热门搜索|关于|联系', name_strip, re.I):
                continue
            page_channels.append(name_strip)

        # 去重
        page_channels = list(set(page_channels))
        print(f"  📊 后台分拣完毕 -> 从剪贴板中成功清洗出有效台数: {len(page_channels)} 个")
        
        for ch_name in page_channels:
            ch_clean = clean_channel_name(ch_name)
            if not ch_clean or len(ch_clean) <= 1: continue
                
            group_clean = regex_classify_channel(ch_clean, t_url)
            if group_clean == "KILL_TRASH": continue

            group_dict[ch_name] = group_clean

    # 注入核心央视资产底座
    for i in range(1, 18): group_dict[f"CCTV-{i}"] = "央视频道"
    group_dict["CCTV-5+"] = "央视频道"

    with open(GROUP_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(group_dict, f, indent=2, ensure_ascii=False)

    duration = time.time() - start_time
    
    print("\n==========================================")
    print("         Group.py 协同分拣完成报告")
    print("==========================================")
    print(f"- 本次任务执行状态    : [成功完成]")
    print(f"- 本次实际联网请求数  : 0 次 (100% 安全防封杀)")
    print(f"- 当前大集群收录电台  : {len(group_dict)} 个频道")
    print(f"- 任务总消耗时长      : {duration:.2f} 秒")
    print(f"- 生产成果存放路径    : {os.path.basename(GROUP_JSON_PATH)}")
    print("==========================================\n")

if __name__ == "__main__":
    main()