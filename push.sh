#!/bin/bash

# =====================================================================
# IPTV Auto Push Tool (Mac/Linux Version)
# =====================================================================

# 1. 定位到脚本当前所在目录 (对应 cd /d "%~dp0")
cd "$(dirname "$0")"
BASE_DIR=$(pwd)

# 颜色高亮定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}===========================================${NC}"
echo -e "${BLUE}    IPTV Auto Push Tool (Unix Version)     ${NC}"
echo -e "${BLUE}===========================================${NC}"

# 2. 智能选择 Python 解释器 (优先使用虚拟环境)
echo -e "${YELLOW}[1/6] Checking Python environment...${NC}"
if [ -f "./.venv/bin/python3" ]; then
    PYTHON_CMD="./.venv/bin/python3"
    echo -e "Using Virtual Environment: ${GREEN}${PYTHON_CMD}${NC}"
else
    PYTHON_CMD="python3"
    echo -e "Using System Python: ${GREEN}${PYTHON_CMD}${NC}"
fi

# 3. 检查 iptvname/nameoriginal.txt 是否有变动
echo -e "${YELLOW}[2/6] Checking iptvname updates...${NC}"
if [ -f "iptvname/nameoriginal.txt" ]; then
    echo "Updating iptvname database..."
    cd "${BASE_DIR}/iptvname"
    $PYTHON_CMD name.py
    cd "${BASE_DIR}"
fi

# 4. 检查 group/group.json 是否有变动
echo -e "${YELLOW}[3/6] Checking group updates...${NC}"
if [ -f "group/group.json" ]; then
    echo "Converting group standard configurations..."
    cd "${BASE_DIR}/group"
    $PYTHON_CMD convert.py
    cd "${BASE_DIR}"
fi

# 5. 运行爬虫核心脚本
echo -e "${YELLOW}[4/6] Running crawl.py...${NC}"
$PYTHON_CMD crawl.py
if [ $? -ne 0 ]; then
    echo -e "${RED}[ERROR] crawl.py failed. Please check dependencies.${NC}"
    exit 1
fi

# 6. 检查文件变动 (等同于 for /f "tokens=*" %%i in ('git status --porcelain'))
echo -e "${YELLOW}[5/6] Checking for changes...${NC}"
git add .
CHANGES=$(git status --porcelain)

if [ -z "$CHANGES" ]; then
    echo -e "${GREEN}[SKIP] No changes detected, nothing to push.${NC}"
    exit 0
fi

# 7. 提交并推送
echo -e "${BLUE}Detected changes. Preparing to push to GitHub...${NC}"
DEFAULT_MSG="Update IPTV list: $(date +'%Y-%m-%d %H:%M:%S')"

echo -e "${YELLOW}Enter Commit Message (Press Enter for default):${NC}"
read msg

if [ -z "$msg" ]; then
    msg=$DEFAULT_MSG
fi

echo -e "${YELLOW}[6/6] Pushing to GitHub...${NC}"
git commit -m "$msg"

# 自动获取当前所在的分支名 (完美对齐 Windows 端的自动获取分支逻辑)
BRANCH=$(git rev-parse --abbrev-ref HEAD)
git push origin "$BRANCH"

if [ $? -eq 0 ]; then
    echo -e "\n${BLUE}===========================================${NC}"
    echo -e "${GREEN} SUCCESS: Pushed to ${BRANCH} branch.${NC}"
    echo -e "${BLUE}===========================================${NC}"
else
    echo -e "\n${RED} ERROR: Push failed. Check your network or Git config.${NC}"
    exit 1
fi
