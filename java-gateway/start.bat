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
rem findstr 的坑（实测确认）：/r 是正则，但多个模式以空格分隔（OR 语义），
rem 带空格的正则必须配 /c: 才被当成「一个模式」。
rem 写成 findstr /r ":5432 .*LISTENING" 会退化成两个模式（":5432" 或 ".*LISTENING"）
rem → 匹配所有 LISTENING 行 → 检查恒真、PG 没起也不报警告。
netstat -ano | findstr /r /c:":5432 .*LISTENING" >nul
if errorlevel 1 (
    echo   [警告] 5432 未监听 —— 网关连不上 PostgreSQL 会启动失败。
    echo          在仓库根目录执行: docker compose up -d postgres
    echo          注意容器要把端口映射到宿主机（5432:5432），只 EXPOSE 不够。
)
netstat -ano | findstr /r /c:":6379 .*LISTENING" >nul
if errorlevel 1 (
    echo   [警告] 6379 未监听 —— Redis 没起（上下文热层/锁/限流会降级，功能仍可用）。
    echo          在仓库根目录执行: docker compose up -d redis
)

echo [1/3] 编译项目...
call mvn compile -q
rem 用 if errorlevel 1（读实时值）而不是 if %errorlevel% neq 0（解析期展开）：
rem 后者一旦被挪进 if(...) 块里就会静默失效
if errorlevel 1 (
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
