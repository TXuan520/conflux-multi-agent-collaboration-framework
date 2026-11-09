@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "EXTRA_ARGS="
if /I "%~1"=="--demo" set "EXTRA_ARGS=-Demo"
if /I "%~1"=="-Demo" set "EXTRA_ARGS=-Demo"
if /I "%~1"=="--shortcut-only" set "EXTRA_ARGS=-CreateShortcutOnly"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch-conflux.ps1" %EXTRA_ARGS%
if errorlevel 1 (
    echo.
    echo [Conflux] 启动异常，请确认已安装 Python 3.11+。
    pause
)
