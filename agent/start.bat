@echo off
chcp 65001 >nul
title Deep Research Agent

rem 切到本脚本所在目录 —— 原先硬编码 cd /d "D:\deep_research\agent"，
rem 换台机器（或换个 clone 路径）就直接失败。%~dp0 始终是本 .bat 所在目录。
cd /d "%~dp0"

echo.
echo ============================================
echo   Deep Research Agent - 启动中...
echo ============================================
echo.
echo   API 地址: http://localhost:8000
echo   Swagger:  http://localhost:8000/docs
echo   健康检查: http://localhost:8000/health
echo.
echo   按 Ctrl+C 停止服务
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 找不到 .venv，请先在 agent 目录执行：
    echo     python -m venv .venv
    echo     .venv\Scripts\pip install -e .
    pause
    exit /b 1
)

if not exist ".env" (
    echo [提示] 未找到 .env，LLM/搜索将因缺少 API Key 失败。
    echo         请执行:  copy .env.example .env   然后填入 Key。
    echo.
)

set PYTHONUTF8=1
rem 让 -m researcher.server 在未 pip install -e . 时也能找到包
set PYTHONPATH=%~dp0src
.venv\Scripts\python -m researcher.server

pause
