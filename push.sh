#!/bin/bash

# --- 颜色定义 ---
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # 无颜色

echo -e "${BLUE}==== 📺 IPTV 自动化同步程序 ====${NC}"

# 1. 运行 Python 脚本抓取数据
echo -e "${YELLOW}[1/3] 正在运行爬虫进行深度分析...${NC}"
python3 crawl.py

# 检查上一步是否成功
if [ $? -ne 0 ]; then
    echo "❌ 爬虫运行出错，请检查网络或依赖。"
    exit 1
fi

# 2. 检查是否有文件更新
echo -e "${YELLOW}[2/3] 检查文件变动...${NC}"
status=$(git status --porcelain)

if [ -z "$status" ]; then
    echo -e "${GREEN}✨ 文件没有变动，无需更新 GitHub。${NC}"
    exit 0
fi

# 3. 提交并推送
echo -e "${BLUE}检测到以下变动：${NC}"
echo "$status"

# 提示输入提交备注
echo -e "${YELLOW}请输入本次更新的备注 (直接回车将使用默认备注):${NC}"
read msg

if [ -z "$msg" ]; then
    msg="自动更新 IPTV 列表: $(date +'%Y-%m-%d %H:%M:%S')"
fi

echo -e "${YELLOW}[3/3] 正在推送到 GitHub...${NC}"
git add .
git commit -m "$msg"
git push origin main

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ 同步成功！你的 APTV 稍后即可刷新。${NC}"
else
    echo "❌ 推送失败，请检查 Token 或网络。"
fi