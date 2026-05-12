#!/bin/bash

# 1. 自动定位脚本所在目录（核心：解决从不同路径调用脚本的问题）
cd "$(dirname "$0")"

# --- 颜色定义 ---
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}==== 📺 IPTV 自动同步工具 (Mac/Ubuntu 通用) ====${NC}"

# 2. 智能选择 Python 解释器
# 优先使用当前目录下的虚拟环境，如果没有，则退而求其次使用系统 python3
if [ -f "./.venv/bin/python3" ]; then
    PYTHON_CMD="./.venv/bin/python3"
else
    PYTHON_CMD="python3"
fi

echo -e "${YELLOW}[1/3] 正在运行爬虫 (使用: $PYTHON_CMD)...${NC}"
$PYTHON_CMD crawl.py

if [ $? -ne 0 ]; then
    echo "❌ 脚本运行失败，请检查依赖。"
    exit 1
fi

# 3. 检查文件变动
echo -e "${YELLOW}[2/3] 检查文件变动...${NC}"
status=$(git status --porcelain)

if [ -z "$status" ]; then
    echo -e "${GREEN}✨ 内容无变化，无需提交。${NC}"
    exit 0
fi

# 4. 提交并推送
echo -e "${BLUE}检测到更新，准备推送至 GitHub...${NC}"

# 如果是在无人值守环境（如定时任务），可以取消下行注释并删除 read 行
# msg="Auto Update: $(date +'%Y-%m-%d %H:%M:%S')"

echo -e "${YELLOW}请输入 Commit 备注 (直接回车使用默认值):${NC}"
read msg
if [ -z "$msg" ]; then
    msg="Update IPTV list: $(date +'%Y-%m-%d %H:%M:%S')"
fi

git add .
git commit -m "$msg"
git push origin main

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ 所有操作已完成！${NC}"
else
    echo -e "❌ 推送失败。如果是首次在 Ubuntu 运行，请确保已配置 Token。"
fi
