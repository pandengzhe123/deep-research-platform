@echo off
chcp 65001 >nul
title Deep Research Java Gateway

cd /d "D:\deep_research\java-gateway"

echo.
echo ============================================
echo   Deep Research Java Gateway - 构建 & 启动
echo ============================================
echo.

echo [1/2] 编译项目...
call mvn compile -q
if %errorlevel% neq 0 (
    echo 编译失败！请确认已安装 Maven 和 JDK 21。
    pause
    exit /b 1
)
echo   编译成功

echo [2/2] 启动网关...
echo.
echo   Web UI:  http://localhost:8080
echo   API:     http://localhost:8080/api
echo   健康检查: http://localhost:8080/api/health
echo.
echo   按 Ctrl+C 停止
echo ============================================
echo.

call mvn spring-boot:run

pause
