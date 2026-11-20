@echo off
chcp 65001 >nul
title 开启 8000 端口转发（让同网络的人访问虚拟机里的项目）
echo ================================================
echo  把本机 8000 端口转发到虚拟机 192.168.75.128:8000
echo  执行后，同一网络的电脑访问：
echo     http://本机IP:8000/
echo  本机当前 IP 请用 ipconfig 查看（例如 10.200.131.111）
echo ================================================
echo.

net start iphlpsvc >nul 2>&1

echo [1/3] 清理旧的转发规则...
netsh interface portproxy delete v4tov4 listenport=8000 listenaddress=0.0.0.0 >nul 2>&1

echo [2/3] 添加端口转发 0.0.0.0:8000 -^> 192.168.75.128:8000 ...
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=8000 connectaddress=192.168.75.128 connectport=8000

echo [3/3] 放行防火墙 8000 端口...
netsh advfirewall firewall delete rule name="TravelDemo8000" >nul 2>&1
netsh advfirewall firewall add rule name="TravelDemo8000" dir=in action=allow protocol=TCP localport=8000

echo.
echo ================ 当前转发规则 ================
netsh interface portproxy show all
echo =============================================
echo.
echo 完成。同一网络的其他电脑请访问 http://本机IP:8000/
echo 查看本机 IP：
ipconfig | findstr /i "IPv4"
echo.
pause
