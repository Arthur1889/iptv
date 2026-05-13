@echo off
setlocal enabledelayedexpansion
cls

echo ===========================================
echo    IPTV Auto Push Tool (Windows Version)
echo ===========================================

:: 1. 定位到脚本所在目录
cd /d "%~dp0"

:: 2. 智能选择 Python 解释器 (优先使用虚拟环境)
echo [1/4] Checking Python environment...
set "PYTHON_CMD=python"
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_CMD=.venv\Scripts\python.exe"
    echo Using Virtual Environment: !PYTHON_CMD!
) else (
    echo Using System Python.
)

:: 3. 运行爬虫脚本
echo [2/4] Running crawl.py...
"!PYTHON_CMD!" crawl.py
if %errorlevel% neq 0 (
    echo [ERROR] crawl.py failed. Please check dependencies.
    pause
    exit /b 1
)

:: 4. 检查文件变动 (等同于 push.sh 的 git status --porcelain)
echo [3/4] Checking for changes...
git add .
for /f "tokens=*" %%i in ('git status --porcelain') do set "CHANGES=%%i"

if "%CHANGES%"=="" (
    echo [SKIP] No changes detected, nothing to push.
    pause
    exit /b 0
)

:: 5. 提交并推送
echo Detected changes. Preparing to push to GitHub...
set "DEFAULT_MSG=Update IPTV list: %date% %time%"
set /p msg="Enter Commit Message (Press Enter for default): "

if "%msg%"=="" set "msg=%DEFAULT_MSG%"

echo [4/4] Pushing to GitHub...
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