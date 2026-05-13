@echo off
:: 设置字符集为UTF-8防止中文乱码
chcp 65001 >nul
cls

echo ===========================================
echo       IPTV 项目自动推送工具 (增强版)
echo ===========================================
echo.

:: 1. 检查是否在 Git 仓库中
if not exist .git (
    echo [错误] 当前目录不是 Git 仓库！
    pause
    exit /b
)

:: 2. 检查当前文件状态
echo [1/4] 正在检查文件变更...
git status -s
echo.

:: 检查是否有需要提交的变更
git diff --quiet && git diff --cached --quiet
if %errorlevel% equ 0 (
    echo [提示] 没有检测到任何文件变更，无需推送。
    echo.
    pause
    exit /b
)

:: 3. 提示用户输入提交信息
set /p msg="请输入本次更新说明 (直接回车使用默认说明): "
if "%msg%"=="" set msg="更新 IPTV 脚本和频道列表"

echo.
echo [2/4] 正在添加文件到暂存区...
git add .

echo.
echo [3/4] 正在提交变更...
git commit -m "%msg%"

echo.
echo [4/4] 正在推送到 GitHub...
:: 获取当前分支名称，确保兼容性
for /f "tokens=*" %%i in ('git rev-parse --abbrev-ref HEAD') do set branch=%%i
git push origin %branch%

:: 4. 结果判定
if %errorlevel% equ 0 (
    echo.
    echo ===========================================
    echo ✅ 推送成功！分支: %branch%
    echo ===========================================
) else (
    echo.
    echo ===========================================
    echo ❌ 推送失败，请检查网络或 Git 配置。
    echo ===========================================
)

echo.
pause