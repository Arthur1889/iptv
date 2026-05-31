import json

def convert_group_json(input_path, output_path):
    # 读取你现在的 group.json
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    new_data = {}
    
    # 执行反转逻辑
    for group_name, channels_str in data.items():
        # 将逗号分隔的字符串切割为列表，并去掉多余空格
        channels = [c.strip() for c in channels_str.split(',')]
        for channel in channels:
            if channel:  # 确保不是空字符串
                new_data[channel] = group_name
                
    # 保存为新的标准 JSON
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(new_data, f, indent=2, ensure_ascii=False)
    
    print(f"转换成功！新文件已保存至: {output_path}")

# 执行转换
convert_group_json('group.json', 'group_standard.json')