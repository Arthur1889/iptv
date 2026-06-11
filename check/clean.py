import re
import os

# 这里写你那个混乱文件的名字
dirty_file = 'dead_gateways.json'
clean_file = 'dead_gateways.txt'

if not os.path.exists(dirty_file):
    print(f"找不到 {dirty_file}，请检查文件名。")
else:
    print("🧹 开始清洗黑名单数据...")
    with open(dirty_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # 正则表达式：精准抓取所有的 "IP" 或 "IP:端口"
    # 例如：124.70.150.60 或者 124.70.150.60:9050
    ip_pattern = re.compile(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?')
    
    # 提取并使用 set 自动去重
    extracted_ips = set(ip_pattern.findall(content))

    # 写入全新的干净 TXT 文件
    with open(clean_file, 'w', encoding='utf-8') as f:
        for ip in sorted(extracted_ips):
            f.write(f"{ip}\n")

    print(f"✅ 清洗完成！成功从混乱文件中榨取出 {len(extracted_ips)} 个纯净的死网关 IP！")
    print(f"📁 已保存为 {clean_file}。你可以把旧的 {dirty_file} 删掉了。")
