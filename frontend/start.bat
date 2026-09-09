@echo off
chcp 65001 >nul
title Deep Research Frontend

rem 切到本脚本所在目录 —— 原先硬编码 cd /d "D:\deep_research\frontend"
cd /d "%~dp0"

echo.
echo ============================================
echo   Deep Research Frontend - Vue 3 + Vite
echo ============================================
echo.
echo   前端: http://localhost:3000
echo   代理: /api -^> :8080 (Java), /kb -^> :8000 (Python)
echo.
echo   按 Ctrl+C 停止
echo ============================================
echo.

if not exist "node_modules" (
    echo [提示] 未找到 node_modules，正在安装依赖...
    call npm install
    if errorlevel 1 (
        echo [错误] npm install 失败
        pause
        exit /b 1
    )
)

call npm run dev

pause
