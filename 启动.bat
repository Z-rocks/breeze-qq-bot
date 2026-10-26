@echo off
chcp 65001 >nul
title Breeze QQ Bot
cd /d "%~dp0"

set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY if exist "C:\Users\adm\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe" set "PY=C:\Users\adm\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe"

if not defined PY (
    echo.
    echo   找不到 Python 3.10+。
    echo   请安装 Python 或把它加入 PATH，然后重试。
    echo.
    pause
    exit /b 1
)

"%PY%" start.py %*
echo.
pause
