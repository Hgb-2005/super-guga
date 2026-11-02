@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "BG_PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%BG_PYTHON%" (
  echo 未找到本机 Python 3.12。请从当前开发环境运行 tools/bg_core.py。
  pause
  exit /b 2
)
echo 独立战棋核心诊断：只读取日志和棋盘，不启动桌宠，不执行游戏动作。
"%BG_PYTHON%" -X utf8 tools\bg_core.py probe --memory --output runtime\bg-core-probe-20261023.json
echo.
echo 结果已保存到 runtime\bg-core-probe-20261023.json
pause
