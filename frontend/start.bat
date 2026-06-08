@echo off
chcp 65001 >nul
title Deep Research Frontend

cd /d "D:\deep_research\frontend"

echo.
echo ============================================
echo   Deep Research Frontend - Vue 3 + Vite
echo ============================================
echo.
echo   前端: http://localhost:3000
echo   代理: /api → :8080 (Java), /kb → :8000 (Python)
echo.
echo   按 Ctrl+C 停止
echo ============================================
echo.

call npm run dev

pause
