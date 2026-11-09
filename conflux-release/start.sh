#!/usr/bin/env bash
set -e

echo "========================================================"
echo "  Conflux 多智能体协作台 (Multi-Agent Collaboration Hub)"
echo "========================================================"
echo ""

if command -v python3 >/dev/null 2>&1; then
    PY_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PY_CMD="python"
else
    echo "[错误] 未找到 Python，请先安装 Python 3.11+。"
    exit 1
fi

echo "正在启动协作台服务端 (默认端口 8777)..."
echo "浏览器访问地址: http://127.0.0.1:8777"
echo "按 Ctrl+C 可停止服务。"
echo ""

if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://127.0.0.1:8777" &
elif command -v open >/dev/null 2>&1; then
    open "http://127.0.0.1:8777" &
fi

$PY_CMD scripts/hub.py
