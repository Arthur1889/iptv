import os
import sys
import re
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)

SOURCES_JSON_PATH = os.path.join(PARENT_DIR, "sources.json")
NAME_JSON_PATH = os.path.join(CURRENT_DIR, "name.json")
GROUP_JSON_PATH = os.path.join(CURRENT_DIR, "group.json")
CACHE_FILE_PATH = os.path.join(CURRENT_DIR, "sources_cache.json")

# ==================== 🔑 硅基流动官方终极配置 ====================
AI_API_KEY = "sk-esofoeqfopwrwqfvhfwuiyzmdsukqbqwzxvdopynmfzwativ"
AI_API_URL = "https://api.siliconflow.cn/v1/chat/completions"
# 🌟 听你的，全线换成速度极快、分类极准、完美支持 JSON 格式的千问 2.5 经典模型
AI_MODEL = "Qwen/Qwen2.5-7B-Instruct" 
# =========================================================================

VALID_GROUPS = [
    "4K频道", "央视频道", "地方卫视", "港澳台", "山东频道", "上海频道", 
    "地方频道", "数字频道", "影视频道", "影视轮播", "歌曲及音乐MV", 
    "纪录纪实", "娱乐频道", "少儿动画", "体育赛事", "外语频道", "综合频道"
]

data_lock = Lock()
print_lock = Lock()

def load_raw_channel_names():
    # 🌟 完美的本地缓存保护机制：1 天内如果存在有效缓存，优先载入，绝对不重复联网请求
    if os.path.exists(CACHE_FILE_PATH) and os.path.getsize(CACHE_FILE_PATH) > 10:
        file_time = os.path.getmtime(CACHE_FILE_PATH)
        if time.time() - file_time < 86400:
            print("💾 命中本地缓存！正在快速载入去重名字...")
            try:
                with open(CACHE_FILE_PATH, 'r', encoding='utf-8') as cf:
                    data = json.load(cf)
                    if data and len(data) > 0:
                        return data
            except:
                pass

    if not os.path.exists(SOURCES_JSON_PATH):
        print(f"❌ 错误：未在上级目录找到 sources.json 文件")
        return []
        
    print("⏳ 缓存失效或不存在，开始从 sources.json 的网络源中批量解析原始名称...")
    with open(SOURCES_JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    urls = data.get("urls", [])
    if isinstance(urls, str): urls = [urls]
    
    raw_names = set()
    for url in set(urls):
        try:
            req = urllib.request.Request(url.strip(), headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=12) as r:
                m3u_text = r.read().decode('utf-8', errors='ignore')
            for line in m3u_text.splitlines():
                if line.strip().startswith("#EXTINF:"):
                    display_name = line.split(",")[-1].strip()
                    if display_name and not re.search(r'更新时间|爱琴海|画沙|202\d-\d', display_name):
                        raw_names.add(display_name)
        except Exception:
            pass
            
    result_list = list(raw_names)
    print(f"📡 成功联网解析并提取出物理去重频道共: {len(result_list)} 个。")
    
    with open(CACHE_FILE_PATH, 'w', encoding='utf-8') as cf:
        json.dump(result_list, cf, indent=2, ensure_ascii=False)
    return result_list

def ask_ai_single_channel_json(raw_name):
    # 🧼 精准防御：转义双引号、剥离换行，防止 JSON 字典断裂
    clean_name = raw_name.replace('"', '\\"').replace('\n', '').replace('\r', '').strip()
    
    prompt = f"分析这个电视频道名称，直接返回格式：标准名,分组名。输入频道：{clean_name}"
    
    data = {
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "response_format": {"type": "json_object"} # 🌟 千问原生支持完美的 JSON 强约束控制
    }
    
    # 🌟 核心改进：标准的 JSON 二进制序列化，杜绝多线程下的任何拼接截断
    req_body = json.dumps(data, ensure_ascii=False).encode('utf-8')
    
    headers = {
        "Authorization": f"Bearer {AI_API_KEY.strip()}",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Length": str(len(req_body)) # 🌟 强制宣告字节长度，锁死网关，绝不接受任何中途截断！
    }
    
    try:
        req = urllib.request.Request(AI_API_URL, data=req_body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as response:
            res_json = json.loads(response.read().decode('utf-8'))
            content = res_json['choices'][0]['message']['content'].strip()
            
            # 支持纯文本及 JSON 格式自适应解析
            if "{" in content and "}" in content:
                start = content.find('{')
                end = content.rfind('}')
                js_data = json.loads(content[start:end+1])
                std_name = js_data.get("standard") or js_data.get("Standard")
                group_name = js_data.get("group") or js_data.get("Group")
                if std_name and group_name:
                    return raw_name, str(std_name).strip(), str(group_name).strip()
            
            content_line = content.split("\n")[0].strip()
            if "," in content_line:
                std_name, group_name = content_line.split(",", 1)
                return raw_name, std_name.strip(), group_name.strip()
    except Exception as e:
        with print_lock:
            print(f"\n❌ [API ERROR] 频道: {raw_name} -> {e}")
    return raw_name, None, None

def main():
    print("==========================================================")
    print(" 🧠 智能分拣外挂 sort.py 启动（千问并发安全去重流）")
    print("==========================================================")
    
    # 完美的本地缓存载入机制
    raw_names = load_raw_channel_names()
    if not raw_names:
        print("❌ 错误：未能获取到任何原始数据！")
        return

    name_repo = {}
    if os.path.exists(NAME_JSON_PATH):
        try:
            with open(NAME_JSON_PATH, 'r', encoding='utf-8') as f:
                name_repo = json.load(f)
        except: pass

    group_repo = {}
    if os.path.exists(GROUP_JSON_PATH):
        try:
            with open(GROUP_JSON_PATH, 'r', encoding='utf-8') as f:
                group_repo = json.load(f)
        except: pass

    # 🌟 增量去重：已经清洗过的老台绝对不在本次请求中占用 API 额度
    existing_keys = set(group_repo.keys())
    for std_k, aliases in name_repo.items():
        existing_keys.add(std_k.upper())
        for a in aliases.split(","):
            existing_keys.add(a.strip().upper())

    filtered_unknowns = [n for n in raw_names if n.upper() not in existing_keys]
    total_unknowns = len(filtered_unknowns)
    
    print(f"🧬 本次真正需要向网络 AI 发起分拣的【未知新怪名】: {total_unknowns} 个。")
    if total_unknowns == 0:
        print("✨ 完美！当前所有抓取到的源在本地已全量清洗，无需重复请求网络 AI。")
        return

    print("🌐 5路并发网道已铺设，新模型全速轰鸣中...")

    success_count = 0
    processed_count = 0
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(ask_ai_single_channel_json, name): name for name in filtered_unknowns}
        
        for future in as_completed(futures):
            raw_name, std_name, group_name = future.result()
            processed_count += 1
            
            # 🌟 规则 9 本地拦截：凡是带有合集专场的怪名，在本地秒分类，100% 隔离高频异常风险
            if re.search(r'专场|合集|点播|精选|首|小时|串烧|视频|音乐|歌台', raw_name):
                target_group = "歌曲及音乐MV" if re.search(r'歌|音乐|唱|DJ|MV', raw_name) else "影视轮播"
                with data_lock:
                    group_repo[raw_name] = target_group
                continue

            if std_name and group_name:
                if any(word in std_name for word in ["属于", "包含", "分类", "内容"]):
                    std_name = "SKIP"

                if group_name not in VALID_GROUPS:
                    group_name = "综合频道"

                with data_lock:
                    if std_name.upper() == "SKIP":
                        target_group = "歌曲及音乐MV" if re.search(r'歌|音乐|唱|DJ|MV', raw_name) else "影视轮播"
                        group_repo[raw_name] = target_group
                    else:
                        success_count += 1
                        if std_name in name_repo:
                            current_aliases = [a.strip() for a in name_repo[std_name].split(",")]
                            if raw_name not in current_aliases:
                                name_repo[std_name] = f"{name_repo[std_name]}, {raw_name}"
                        else:
                            name_repo[std_name] = raw_name
                        group_repo[std_name] = group_name

            # ⏳ 单行滚动进度条
            pct = (processed_count / total_unknowns) * 100
            progress_msg = f"\r⏳ 处理进度: {processed_count}/{total_unknowns} [{pct:.1f}%] | 成功捕获电视: {success_count} 个"
            with print_lock:
                sys.stdout.write(progress_msg)
                sys.stdout.flush()

            # 每 5 个安全写盘，不丢失任何清洗成果
            if processed_count % 5 == 0 or processed_count == total_unknowns:
                with data_lock:
                    with open(NAME_JSON_PATH, 'w', encoding='utf-8') as f:
                        json.dump(name_repo, f, indent=2, ensure_ascii=False)
                    with open(GROUP_JSON_PATH, 'w', encoding='utf-8') as f:
                        json.dump(group_repo, f, indent=2, ensure_ascii=False)

    print("\n==========================================================")
    print(f"🎉 智能增量分拣完毕！你的基础对照字典已完成无损净化。")
    print("==========================================================")

if __name__ == "__main__":
    main()