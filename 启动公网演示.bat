@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 旅游项目 - 公网演示启动器

echo ============================================
echo   旅游数据平台 · 公网演示一键启动
echo ============================================
echo.

REM ---------- 1. 检查后端服务 ----------
echo [1/3] 检查本地服务 http://127.0.0.1:8000 ...
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:8000/healthz > tmp_code.txt 2>nul
set /p CODE=<tmp_code.txt
del tmp_code.txt >nul 2>&1

if "%CODE%"=="200" (
  echo       本地服务已在运行，跳过启动。
) else (
  echo       本地服务未运行，正在启动后端...
  start "旅游后端服务" /min cmd /c "python -m uv run -m app.main"
  echo       等待后端就绪...
  :waitloop
  timeout /t 2 >nul
  curl -s -o nul -w "%%{http_code}" http://127.0.0.1:8000/healthz > tmp_code.txt 2>nul
  set /p CODE=<tmp_code.txt
  del tmp_code.txt >nul 2>&1
  if "%CODE%"=="200" goto :ready
  goto :waitloop
)
:ready
echo       本地服务 OK ^(200^)

REM ---------- 2. 启动公网隧道 ----------
echo [2/3] 正在建立公网隧道，请稍候（约 5-15 秒）...
if exist tunnel_url.txt del tunnel_url.txt >nul 2>&1
start "公网隧道(请勿关闭)" /min cmd /c "cloudflared.exe tunnel --url http://localhost:8000 > tunnel.log 2>&1"

REM ---------- 3. 抓取公网地址（用 PowerShell 精确提取，避免 findstr 正则转义问题）----------
echo       等待公网地址分配...
:urlloop
timeout /t 2 >nul
if not exist tunnel.log goto :urlloop
powershell -NoProfile -ExecutionPolicy Bypass -Command "$m = Select-String -Path 'tunnel.log' -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1; if ($m) { $m.Matches[0].Value | Set-Content -Path 'tunnel_url.txt' -NoNewline }" >nul 2>&1
if exist tunnel_url.txt goto :goturl
goto :urlloop
:goturl
set /p PUBURL=<tunnel_url.txt

echo.
echo ============================================
echo   公网地址已就绪：
echo.
echo        %PUBURL%
echo.
echo   已复制到剪贴板，并自动打开浏览器。
echo   把这个网址发给任何人、任何网络都能访问。
echo ============================================
echo.
echo 提示：此窗口和"公网隧道"窗口不要关闭，关闭后网址失效。
echo       答辩结束后运行"停止公网演示.bat"清理。
echo.

REM 复制到剪贴板 + 打开浏览器
echo|set /p="%PUBURL%" | clip >nul 2>&1
start "" "%PUBURL%"

pause
