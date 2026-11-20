@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 旅游项目 - 公网演示停止器

echo ============================================
echo   旅游数据平台 · 停止公网演示
echo ============================================
echo.

echo [1/2] 关闭公网隧道...
taskkill /F /IM cloudflared.exe >nul 2>&1
if %errorlevel%==0 (echo       隧道已关闭) else (echo       没有运行中的隧道)

echo [2/2] 关闭后端服务（监听 8000 端口的进程）...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
  taskkill /F /PID %%p >nul 2>&1
  echo       已关闭后端进程 PID=%%p
)

del tunnel.log >nul 2>&1
del tunnel_url.txt >nul 2>&1
del tmp_code.txt >nul 2>&1

echo.
echo 清理完成。下次演示双击"启动公网演示.bat"即可。
echo.
pause
