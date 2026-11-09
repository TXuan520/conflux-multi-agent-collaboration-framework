@echo off
chcp 65001 >nul
title Conflux Collaboration Hub

echo ========================================================
echo   Conflux 多智能体协作台 (Multi-Agent Collaboration Hub)
echo ========================================================
echo.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未在系统 PATH 中找到 Python，请先安装 Python 3.11+ 并勾选 "Add to PATH"。
    pause
    exit /b 1
)

echo 正在启动协作台服务端 (默认端口 8777)...
echo 浏览器访问地址: http://127.0.0.1:8777
echo 按 Ctrl+C 可停止服务。
echo.

start "" "http://127.0.0.1:8777"
python scripts\hub.py
if %errorlevel% neq 0 (
    echo.
    echo 服务端异常退出，退出码: %errorlevel%
    pause
)
