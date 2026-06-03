@echo off
setlocal enabledelayedexpansion
cls

echo ===========================================
echo    IPTV Auto Push Tool (Windows Version)
echo ===========================================

:: 1. 定位到脚本所在目录
cd /d "%~dp0"

:: 2. 智能选择 Python 解释器 (优先使用虚拟环境)
echo [1/6] Checking Python environment...
set "PYTHON_CMD=python"
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_CMD=.venv\Scripts\python.exe"
    echo Using Virtual Environment: !PYTHON_CMD!
) else (
    echo Using System Python.
)

:: 3. 检查 iptvname/nameoriginal.txt 是否有变动
echo [2/6] Checking iptvname updates...
if exist "iptvname\nameoriginal.txt" (
    echo Running name.py updates...
    cd /d "%~dp0\iptvname"
    :: 🌟【核心修复】：使用锁定的虚拟环境引擎运行，防止依赖丢失
    "!PYTHON_CMD!" name.py
    cd /d "%~dp0"
)

:: 4. 检查 group/group.json 是否有变动
echo [3/6] Checking group updates...
if exist "group\group.json" (
    echo Running convert.py updates...
    cd /d "%~dp0\group"
    :: 🌟【核心修复】：同上，强制使用虚拟环境环境安全沙盒
    "!PYTHON_CMD!" convert.py
    cd /d "%~dp0"
)

:: 5. 运行爬虫脚本
echo [4/6] Running crawl.py...
"!PYTHON_CMD!" crawl.py
if %errorlevel% neq 0 (
    echo [ERROR] crawl.py failed. Please check dependencies or network.
    pause
    exit /b 1
)

:: 6. 检查文件变动 (等同于 push.sh 的 git status --porcelain)
echo [5/6] Checking for changes...
git add .
set "CHANGES="
for /f "tokens=*" %%i in ('git status --porcelain') do set "CHANGES=%%i"

if "%CHANGES%"=="" (
    echo [SKIP] No changes detected, nothing to push.
    pause
    exit /b 0
)

:: 7. 提交并推送
echo Detected changes. Preparing to push to GitHub...
set "DEFAULT_MSG=Update IPTV list: %date% %time%"
set /p msg="Enter Commit Message (Press Enter for default): "

if "%msg%"=="" set "msg=%DEFAULT_MSG%"

echo [6/6] Pushing to GitHub...
git commit -m "%msg%"

:: 自动获取当前分支名
for /f "tokens=*" %%i in ('git rev-parse --abbrev-ref HEAD') do set branch=%%i
git push origin %branch%

if %errorlevel% equ 0 (
    echo.
    echo ===========================================
    echo  SUCCESS: Pushed to %branch% branch.
    echo ===========================================
) else (
    echo.
    echo  ERROR: Push failed. Check your network or Git config.
)

pause
