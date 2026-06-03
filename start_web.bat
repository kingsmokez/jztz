@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo 正在检查虚拟环境...
if not exist ".venv" (
    echo 创建虚拟环境...
    python -m venv .venv
)

echo 激活虚拟环境...
call .venv\Scripts\activate.bat

echo 检查依赖...
pip install -r requirements.txt -q

echo.
echo ========================================
echo   价值投资之王 - 智能选股系统
echo   访问 http://localhost:5559
echo ========================================
echo.

python web_app.py
pause