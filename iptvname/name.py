import requests
import re
import json

def clean_text(text):
    """清除无意义字符，用于匹配"""
    # 移除后缀名、括号内容及常见杂质词
    text = re.sub(r'\.png$', '', text, flags=re.I)
    text = re.sub(r'[\[\(\（\【\《].*?[\]\)\）\】\》]', '', text)
    noise = r'(?i)\b(4K|8K|高清|HD|1080P|720P|超清|蓝光|BD|频道|TV)\b'
    text = re.sub(noise, '', text)
    return text.replace('-', '').replace(' ', '').strip().lower()

def get_mapping():
    print("正在获取标准库列表...")
    # 1. 获取标准 ID (从 fanmingming 图标库)
    # 使用 GitHub API 获取文件列表
    api_url = "https://api.github.com/repos/fanmingming/live/contents/tv"
    resp = requests.get(api_url)
    if resp.status_code != 200:
        print("无法访问 GitHub API，请检查网络或代理")
        return
    
    # 提取标准 ID（去掉 .png）
    std_items = {}
    for item in resp.json():
        if item['name'].endswith('.png'):
            std_name = item['name'].replace('.png', '')
            std_items[clean_text(std_name)] = std_name

    print(f"已加载 {len(std_items)} 个标准频道 ID")

    print("正在抓取 iptv-org 原始源数据...")
    # 2. 获取别名候选 (从 iptv-org cn.m3u)
    m3u_url = "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cn.m3u"
    m3u_content = requests.get(m3u_url).text
    
    # 提取 #EXTINF 中的频道名称
    raw_names = re.findall(r'#EXTINF:.*?,(.*)', m3u_content)
    raw_names = list(set(raw_names)) # 去重
    print(f"已发现 {len(raw_names)} 个原始别名候选")

    # 3. 开始匹配
    name_mapping = {}
    for raw in raw_names:
        clean_raw = clean_text(raw)
        for clean_std, original_std in std_items.items():
            # 匹配逻辑：如果清洗后的别名包含清洗后的标准名
            if clean_std and clean_std in clean_raw:
                # 写入格式： "原始别名": "标准名"
                name_mapping[raw.strip()] = original_std
                break 

    # 4. 写入文件
    with open('name.json', 'w', encoding='utf-8') as f:
        json.dump(name_mapping, f, indent=2, ensure_ascii=False)
    
    print(f"处理完成！已生成 name.json，包含 {len(name_mapping)} 条映射关系。")

if __name__ == "__main__":
    get_mapping()
