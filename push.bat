@echo off
:: 设置字符集为UTF-8防止中文乱码
chcp 65001 >nul
cls

echo ============================================
echo       IPTV 项目自动推送工具 (Git)
echo ============================================
echo.

:: 1. 检查当前 Git 状态
echo [1/4] 正在检查文件变更...
git status
echo.

:: 提示用户输入提交信息
set /p msg="请输入本次更新的说明 (直接回车则使用默认说明): "
if "%msg%"=="" set msg="更新 IPTV 脚本和频道列表"

echo.
echo [2/4] 正在添加文件到暂存区...
git add .

echo.
echo [3/4] 正在提交变更...
git commit -m "%msg%"

echo.
echo [4/4] 正在推送到 GitHub (origin main)...
echo --------------------------------------------
git push origin main

if %errorlevel% equ 0 (
    echo.
    echo ============================================
    echo ✅ 推送成功！
    echo ============================================
) else (
    echo.
    echo ============================================
    echo ❌ 推送失败，请检查网络或 Git 配置。
    echo ============================================
)

echo.
pause