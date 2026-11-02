@echo off
chcp 65001 >nul
title 咕嘎桌宠 - 推送到 GitHub
setlocal

set "PY=python"
set "GH=gh"
cd /d "%~dp0"

echo ============================================================
echo   咕嘎桌宠 - 推送到 GitHub
echo ============================================================
echo.
echo 步骤 1: GitHub 设备码登录 (会显示一个代码)
echo   - 浏览器打开 https://github.com/login/device
echo   - 输入显示的代码
echo   - 登录并授权
echo.

"%PY%" gh_login.py
if errorlevel 1 (
    echo [错误] 登录失败
    pause
    exit /b 1
)

echo.
echo 步骤 2: 配置 gh CLI 凭据
"%GH%" auth login --with-token < "%USERPROFILE%\.gh_token.txt"
"%GH%" auth status

echo.
echo 步骤 3: 配置 git 远程
git remote get-url origin >nul 2>&1
if errorlevel 1 (git remote add origin https://github.com/jscjscjscjscjsc/heimao-zhuochong.git) else (git remote set-url origin https://github.com/jscjscjscjscjsc/heimao-zhuochong.git)

echo.
echo 步骤 4: 推送主分支
git push -u origin main
if errorlevel 1 (
    echo [错误] 推送失败
    echo 请先确认 GitHub 登录和 heimao-zhuochong 仓库权限
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   推送成功! 访问:
echo   https://github.com/jscjscjscjscjsc/heimao-zhuochong
echo ============================================================
pause
