@echo off
chcp 65001 >nul
title Open Deep Research

cd /d "D:\open_deep_research-main\open_deep_research-main"

echo.
echo ============================================
echo   Open Deep Research - 启动中...
echo ============================================
echo.
echo   启动后请打开:
echo   https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
echo.
echo   按 Ctrl+C 可以停止服务
echo ============================================
echo.

set PYTHONUTF8=1
uvx --refresh --from "langgraph-cli[inmem]" --with-editable . --python 3.11 langgraph dev --allow-blocking

pause
