import os
import sys
import re
import json
import time
import urllib.request

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)

SOURCES_JSON_PATH = os.path.join(PARENT_DIR, "sources.json")
NAME_JSON_PATH = os.path.join(CURRENT_DIR, "name.json")
GROUP_JSON_PATH = os.path.join(CURRENT_DIR, "group.json")
CACHE_FILE_PATH = os.path.join(CURRENT_DIR, "sources_cache.json")

# ==================== 🔑 硅基流动官方终极配置 ====================
AI_API_KEY = "sk-esofoeqfopwrwqfvhfwuiyzmdsukqbqwzxvdopynmfzwativ" 
AI_API_URL = "https://api.siliconflow.cn/v1/chat/completions"
AI_MODEL = "vendor/DeepSeek-R1-Distill-Qwen-7B" 
# =========================================================================

VALID_GROUPS = [
    "4K频道", "央视频道", "地方卫视", "港澳台", "山东频道", "上海频道", 
    "地方频道", "数字频道", "影视频道", "影视轮播", "歌曲及音乐MV", 
    "纪录纪实", "娱乐频道", "少儿动画", "体育赛事", "外语频道", "综合频道"
]

def load_raw_channel_names():
    if os.path.exists(CACHE_FILE_PATH):
        file_time = os.path.getmtime(CACHE_FILE_PATH)
        if time.time() - file_time < 86400:
            print("💾 命中本地缓存！正在快速载入去重名字...")
            with open(CACHE_FILE_PATH, 'r', encoding='utf-8') as cf:
                return json.load(cf)

    if not os.path.exists(SOURCES_JSON_PATH):
        print(f"❌ 错误：未在上级目录找到 sources.json 文件")
        return []
        
    with open(SOURCES_JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    urls = data.get("urls", [])
    if isinstance(urls, str): urls = [urls]
    
    raw_names = set()
    print("⏳ 开始从 sources.json 的网络源中批量解析原始名称...")
    for url in set(urls):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as r:
                m3u_text = r.read().decode('utf-8', errors='ignore')
            for line in m3u_text.splitlines():
                if line.strip().startswith("#EXTINF:"):
                    display_name = line.split(",")[-1].strip()
                    if display_name and not re.search(r'更新时间|爱琴海|画沙', display_name):
                        raw_names.add(display_name)
        except Exception:
            pass
            
    result_list = list(raw_names)
    with open(CACHE_FILE_PATH, 'w', encoding='utf-8') as cf:
        json.dump(result_list, cf, indent=2, ensure_ascii=False)
    return result_list

def ask_ai_single_channel_json(raw_name):
    system_instruction = (
        "You are an expert IPTV metadata processing assistant.\n"
        "Your sole task is to classify the provided channel name into one of the allowed categories and provide its standard Chinese name.\n"
        "CRITICAL RULE FOR REEL/COLLECTION (要求9):\n"
        "If the input name is NOT a television station channel, but a personal movie collection, a song playlist, music MV, loop streaming, or celebrity專場 (e.g., '周杰伦精选', '林正英电影', '美女短视频', 'DJ舞曲串烧'), you MUST set the 'standard' name strictly to 'SKIP'.\n\n"
        "You must respond with a valid JSON object matching this schema exactly, do not add any conversation or explanations:\n"
        '{"standard": "Standard Chinese Name or SKIP", "group": "One of the 17 allowed group names"}'
    )
    
    user_input = (
        f"Input Channel Name: {raw_name}\n"
        f"Allowed Group List: {json.dumps(VALID_GROUPS, ensure_ascii=False)}\n"
        "Output JSON:"
    )
    
    headers = {
        "Authorization": f"Bearer {AI_API_KEY.strip()}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_input}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1
    }
    
    try:
        req = urllib.request.Request(AI_API_URL, data=json.dumps(data).encode('utf-8'), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=12) as response:
            res_json = json.loads(response.read().decode('utf-8'))
            content = res_json['choices'][0]['message']['content'].strip()
            
            if "<think>" in content or "</think>" in content:
                content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            content = re.sub(r'^```json\s*|```$', '', content, flags=re.I).strip()
            
            result = json.loads(content)
            return raw_name, result.get("standard"), result.get("group")
    except Exception:
        return raw_name, None, None

def main():
    print("==========================================================")
    print(" 🧠 智能分拣外挂 sort.py 启动（Crawl同款单行进度条流）")
    print("==========================================================")
    
    raw_names = load_raw_channel_names()
    print(f"📈 累计载入物理去重原始频道名称: {len(raw_names)} 个。")
    
    if not raw_names:
        return

    name_repo = {}
    if os.path.exists(NAME_JSON_PATH):
        try:
            with open(NAME_JSON_PATH, 'r', encoding='utf-8') as f: name_repo = json.load(f)
        except: pass
        
    group_repo = {}
    if os.path.exists(GROUP_JSON_PATH):
        try:
            with open(GROUP_JSON_PATH, 'r', encoding='utf-8') as f: group_repo = json.load(f)
        except: pass

    # 🌟 修复底层漏网之鱼：严格把本地已经分类或作为别名的老频道统统踢出去！
    existing_keys = set(group_repo.keys())
    for std_k, aliases in name_repo.items():
        existing_keys.add(std_k.upper())
        for a in aliases.split(","):
            existing_keys.add(a.strip().upper())
            
    filtered_unknowns = [n for n in raw_names if n.upper() not in existing_keys]
    total_unknowns = len(filtered_unknowns)
    print(f"🧬 本次真正需要向网络 AI 发起分拣的【未知新怪名】: {total_unknowns} 个。")
    
    if total_unknowns == 0:
        print("✨ 完美！当前所有抓取到的源在本地已全量清洗，无需向网络AI查询。")
        return

    print("🌐 开始同步单行推进，AI 深度语义判定中...")
    success_count = 0
    processed_count = 0
    
    for name in filtered_unknowns:
        raw_name, std_name, group_name = ask_ai_single_channel_json(name)
        processed_count += 1
        
        if std_name and group_name:
            # 🛑 拦截话痨回复：如果标准名字长度超过8个字，或者带有解释客套，直接强制归入 SKIP 判定
            if len(std_name) > 8 or any(word in std_name for word in ["属于", "是", "包含", "内容", "分类"]):
                std_name = "SKIP"

            if group_name not in VALID_GROUPS:
                group_name = "综合频道"

            # 规则 9：如果是歌曲、电影合集或专场流，AI 返回了 SKIP，或者名字包含特征词
            if std_name.upper() == "SKIP" or re.search(r'专场|合集|点播|精选|首|小时|串烧|视频|音乐|歌台', raw_name):
                # 智能分类：包含歌舞字眼归入歌曲，否则一律影视轮播
                target_group = "歌曲及音乐MV" if re.search(r'歌|音乐|唱|DJ|MV', raw_name) else "影视轮播"
                group_repo[raw_name] = target_group
            else:
                # 纯净电视频道才允许进入别名库双写合入
                success_count += 1
                if std_name in name_repo:
                    current_aliases = [a.strip() for a in name_repo[std_name].split(",")]
                    if raw_name not in current_aliases:
                        name_repo[std_name] = f"{name_repo[std_name]}, {raw_name}"
                else:
                    name_repo[std_name] = raw_name
                group_repo[std_name] = group_name

        # 🌟 【Crawl.py 同款进度条】：标准的 \r + sys.stdout.write 锁行渲染，绝不刷屏
        pct = (processed_count / total_unknowns) * 100
        progress_msg = f"\r⏳ 处理进度: {processed_count}/{total_unknowns} [{pct:.1f}%] | 成功捕获电视: {success_count} 个"
        sys.stdout.write(progress_msg)
        sys.stdout.flush()

        # 每 5 个安全回写落地
        if processed_count % 5 == 0 or processed_count == total_unknowns:
            with open(NAME_JSON_PATH, 'w', encoding='utf-8') as f:
                json.dump(name_repo, f, indent=2, ensure_ascii=False)
            with open(GROUP_JSON_PATH, 'w', encoding='utf-8') as f:
                json.dump(group_repo, f, indent=2, ensure_ascii=False)

    print("\n==========================================================")
    print(f"🎉 智能增量分拣完毕！你的基础对照字典已完成无损净化。")
    print("==========================================================")

if __name__ == "__main__":
    main()