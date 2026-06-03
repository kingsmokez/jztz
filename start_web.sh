#!/bin/bash
cd "$(dirname "$0")"

echo "正在检查虚拟环境..."
if [ ! -d ".venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv .venv
fi

echo "激活虚拟环境..."
source .venv/bin/activate

echo "检查依赖..."
pip install -r requirements.txt -q

echo ""
echo "========================================"
echo "  价值投资之王 - 智能选股系统"
echo "  访问 http://localhost:5559"
echo "========================================"
echo ""

python web_app.py
