@echo off
chcp 65001 >nul
title Deep Research Agent

cd /d "D:\deep_research\agent"

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

set PYTHONUTF8=1
.venv\Scripts\python -m researcher.server

pause
