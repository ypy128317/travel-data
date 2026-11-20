@echo off
chcp 65001 >nul
echo === test findstr regex ===
findstr /r "https://.*\.trycloudflare\.com" test_tunnel.log
echo === exit=%errorlevel% ===
for /f "tokens=* delims=" %%i in ('findstr /r "https://.*\.trycloudflare\.com" test_tunnel.log 2^>nul') do (
  echo MATCHED: %%i
  echo %%i > test_url.txt
)
if exist test_url.txt (
  set /p U=<test_url.txt
  for /f "tokens=1" %%u in (test_url.txt) do echo URL_TOKEN: %%u
  del test_url.txt
) else (
  echo NO URL FOUND
)
