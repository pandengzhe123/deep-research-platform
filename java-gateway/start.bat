@echo off
chcp 65001 >nul
title Deep Research Java Gateway

rem 切到本脚本所在目录 —— 原先硬编码 cd /d "D:\deep_research\java-gateway"
cd /d "%~dp0"

echo.
echo ============================================
echo   Deep Research Java Gateway - 构建 ^& 启动
echo ============================================
echo.

echo [0/3] 检查 PostgreSQL (5432) 与 Redis (6379)...
netstat -ano | findstr /r /c:":5432 .*LISTENING" >nul
if errorlevel 1 (
    echo   [警告] 5432 未监听 —— PostgreSQL 可能没起。
    echo          在仓库根目录执行: docker compose up -d postgres
)
netstat -ano | findstr /r /c:":6379 .*LISTENING" >nul
if errorlevel 1 (
    echo   [警告] 6379 未监听 —— Redis 没起（上下文热层/锁/限流会降级，功能仍可用）。
    echo          在仓库根目录执行: docker compose up -d redis
)

echo [1/3] 编译项目...
call mvn compile -q
if %errorlevel% neq 0 (
    echo 编译失败！请确认已安装 Maven 和 JDK 21。
    pause
    exit /b 1
)
echo   编译成功

echo [2/3] 启动网关...
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
