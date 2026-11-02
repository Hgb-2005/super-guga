@echo off
chcp 65001 >nul
cd /d "%~dp0"
python tools\bg_live.py
if errorlevel 1 (
  echo 实战窗口启动失败，请保留本窗口中的错误信息。
  pause
)
