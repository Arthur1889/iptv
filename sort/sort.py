import os
import sys
import re
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# 1. 严格在当前目录下工作，读取上级 sources.json (要求 1 & 2)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)

SOURCES_JSON_PATH = os.path.join(PARENT_DIR, "sources.json")
NAME_JSON_PATH = os.path.join(CURRENT_DIR, "name.json")
GROUP_JSON_PATH = os.path.join(CURRENT_DIR, "group.json")
CACHE_FILE_PATH = os.path.join(CURRENT_DIR, "sources_cache.json")

# ==================== 🔑 硅基流动万无一失通用配置 (要求 3) ====================
AI_API_KEY = "sk-esofoeqfopwrwqfvhfwuiyzmdsukqbqwzxvdopynmfzwativ" 
AI_API_URL = "https://api.siliconflow.cn/v1/chat/completions"
AI_MODEL = "Qwen/Qwen2.5-7B-Instruct"  # 极稳免费全量模型，避开网关 400
# =========================================================================

# 17 个严格属性分组列表 (要求 5)
VALID_GROUPS = [
    "4K频道", "央视频道", "地方卫视", "港澳台", "山东频道", "上海频道", 
    "地方频道", "数字频道", "影视频道", "影视轮播", "歌曲及音乐MV", 
    "纪录纪实", "娱乐频道", "少儿动画", "体育赛事", "外语频道", "综合频道"
]

data_lock = Lock()
print_lock = Lock()

def load_raw_channel_names():
    """
    智能提取源：支持1天（86400秒）本地缓存机制 (要求 2)
    """
    if os.path.exists(CACHE_FILE_PATH):
        file_time = os.path.getmtime(CACHE_FILE_PATH)
        if time.time() - file_time < 86400:
            print("💾 命中本地缓存！正在从 sources_cache.json 快速载入去重名字...")
            with open(CACHE_FILE_PATH, 'r', encoding='utf-8') as cf:
                return json.load(cf)

    if not os.path.exists(SOURCES_JSON_PATH):
        print(f"❌ 错误：未在上级目录找到 sources.json 文件 ({SOURCES_JSON_PATH})")
        return []
        
    with open(SOURCES_JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    urls = data.get("urls", [])
    if isinstance(urls, str): urls = [urls]
    
    raw_names = set()
    print("⏳ 缓存失效：开始从 sources.json 的网络源中批量解析原始名称...")
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
        except Exception as e:
            print(f"   ⚠️ 抓取网络源失败跳过: {url[:30]}... 原因: {e}")
            
    result_list = list(raw_names)
    with open(CACHE_FILE_PATH, 'w', encoding='utf-8') as cf:
        json.dump(result_list, cf, indent=2, ensure_ascii=False)
    print(f"💾 原始名称提取完毕，已成功建立1天周期的本地缓存文件。")
    return result_list

def ask_ai_single_channel(raw_name):
    """
    通过文本流向 AI 提问：融入要求 9 针对合集/专场流的智能拦截逻辑
    """
    prompt = (
        f"你是一个网络电视直播源与影音大数据清洗专家。请对用户给出的脏频道名字进行属性鉴别与分拣转换。\n\n"
        f"【核心鉴别规则】\n"
        f"1. 判断它是不是一个正常的电视频道台（如CCTV、湖南卫视、HBO）。如果是，将其转化为标准中文电视名（如 'CCTV1HD'->'CCTV1'）。\n"
        f"2. ⚠️ 核心注意：如果发现它根本不是电视电视频道，而是一些单独的歌曲、歌曲合集、流行音乐MV、个人电影专场、循环播放的电影合集（如 '周星驰电影'、'刘德华MV合集'、'经典金曲轮播'），这种属于轮播合集。此时你必须将标准中文电视名直接设置为字符串 'SKIP'！\n"
        f"3. 无论属于上述哪种，都必须从下面给定的 17 个合法分组中挑选一个最适合它的归属：\n"
        f"   可选项：{','.join(VALID_GROUPS)}\n"
        f"   （提示：电影合集归入 '影视轮播'，歌曲/MV合集归入 '歌曲及音乐MV'）\n\n"
        f"待分析脏名称: {raw_name}\n\n"
        f"【严格输出格式】\n"
        f"你必须且只能返回单行文本，格式固定为: 标准名称|分组名称\n"
        f"严禁带有任何解释或 Markdown 标记。例如正规台返回: CCTV1|央视频道；如果是歌曲合集返回: SKIP|歌曲及音乐MV"
    )
    
    headers = {
        "Authorization": f"Bearer {AI_API_KEY.strip()}",
        "Content-Type": "application/json"
    }
    data = {
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1
    }
    
    try:
        req = urllib.request.Request(AI_API_URL, data=json.dumps(data).encode('utf-8'), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as response:
            res_json = json.loads(response.read().decode('utf-8'))
            content = res_json['choices'][0]['message']['content'].strip()
            content = re.sub(r'^```json\s*|```$', '', content, flags=re.I).strip()
            
            if "|" in content:
                parts = content.split("|")
                std_name = parts[0].strip()
                group_name = parts[1].strip()
                return raw_name, std_name, group_name
    except Exception:
        pass
    return raw_name, None, None

def main():
    print("==========================================================")
    print(" 🧠 独立智能分拣外挂 sort.py 启动（文本流并发引擎）")
    print("==========================================================")
    
    raw_names = load_raw_channel_names()
    print(f"📈 累计载入物理去重原始频道名称: {len(raw_names)} 个。")
    
    if not raw_names:
        print("ℹ️ 未提取到任何频道名称，程序退出。")
        return

    # 载入或初始化生成文件 (要求 6 & 7)
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

    # 提取别名库中已涵盖的所有原始名字，计算增量
    existing_aliases = set()
    for aliases in name_repo.values():
        for a in aliases.split(","):
            existing_aliases.add(a.strip().upper())
    # 同时也把 group_repo 里已有的名字剔除，防止对已知合集重复查询
    for raw_k in group_repo.keys():
        existing_aliases.add(raw_k.upper())
            
    filtered_unknowns = [n for n in raw_names if n.upper() not in existing_aliases]
    total_unknowns = len(filtered_unknowns)
    print(f"🧬 本次真正需要向网络 AI 发起智能属性分拣的频道数: {total_unknowns} 个。")
    
    if total_unknowns == 0:
        print("✨ 所有频道在当前本地 JSON 中已有配置映射，无需联网。")
        return

    print("🌐 正在建立 5路 并发通道，大模型全速分拣中...")
    success_count = 0
    processed_count = 0
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(ask_ai_single_channel, name): name for name in filtered_unknowns}
        
        for future in as_completed(futures):
            raw_name, std_name, group_name = future.result()
            processed_count += 1
            
            if std_name and group_name:
                success_count += 1
                with data_lock:
                    # 规则 9：如果是歌曲/电影合集专场，AI返回 SKIP，则不写入 name.json
                    if std_name.upper() == "SKIP":
                        # 直接以原始怪名作为 Key 写入分组，不进入标准的电视台别名库
                        if group_name in VALID_GROUPS:
                            group_repo[raw_name] = group_name
                        else:
                            group_repo[raw_name] = "综合频道"
                    else:
                        # 正常电视频道：双写合入 (要求 6 & 7)
                        if std_name in name_repo:
                            current_aliases = [a.strip() for a in name_repo[std_name].split(",")]
                            if raw_name not in current_aliases:
                                name_repo[std_name] = f"{name_repo[std_name]}, {raw_name}"
                        else:
                            name_repo[std_name] = raw_name
                        
                        if group_name in VALID_GROUPS:
                            group_repo[std_name] = group_name
                        else:
                            group_repo[std_name] = "综合频道"
            
            # 要求 8：保持在一行的单行动态刷新进度条，决不刷屏
            pct = (processed_count / total_unknowns) * 100
            bar = '█' * int(20 * processed_count // total_unknowns) + '-' * (20 - int(20 * processed_count // total_unknowns))
            with print_lock:
                sys.stdout.write(f"\r🔍 分拣进度: [{bar}] {pct:.1f}% | 已处理: {processed_count}/{total_unknowns} | 成功分拣: {success_count} 个\033[K")
                sys.stdout.flush()

            # 实时同步持久化保存到当前目录 (要求 1)
            if processed_count % 5 == 0 or processed_count == total_unknowns:
                with data_lock:
                    with open(NAME_JSON_PATH, 'w', encoding='utf-8') as f:
                        json.dump(name_repo, f, indent=2, ensure_ascii=False)
                    with open(GROUP_JSON_PATH, 'w', encoding='utf-8') as f:
                        json.dump(group_repo, f, indent=2, ensure_ascii=False)
            
            time.sleep(0.02)

    print("\n==========================================================")
    print(f"🎉 智能分拣清洗完毕！")
    print(f"📝 纯净电视别名映射已成功保存至当前目录: ./name.json")
    print(f"🗂️ 全量属性分组映射已成功保存至当前目录: ./group.json")
    print("==========================================================")

if __name__ == "__main__":
    main()