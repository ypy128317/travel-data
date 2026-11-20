@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=C:\Users\DAHE\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

:menu
echo.
echo ============================================================
echo   旅游数据平台 - 一键部署到公网
echo   公网地址: http://101.37.234.52
echo ============================================================
echo.
echo   [1] 全量部署 (前端+后端, 改了 .py 用这个)  ~1分钟
echo   [2] 只部署前端 (只改了 html/js, 秒级生效)
echo   [3] 只验证 (检查公网是否正常)
echo   [0] 退出
echo.
set "CH="
set /p "CH=请输入选项 1/2/3/0: "

if "%CH%"=="1" goto full
if "%CH%"=="2" goto frontend
if "%CH%"=="3" goto verify
if "%CH%"=="0" goto end
echo 无效选项，请重新输入
goto menu

:full
"%PY%" deploy.py
goto end

:frontend
"%PY%" deploy.py --frontend
goto end

:verify
"%PY%" deploy.py --verify
goto end

:end
echo.
pause
