@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYLAUNCHER_ALLOW_INSTALL=0"
chcp 65001 >nul
py -3.12 tools\bootstrap.py run tools\launch_client.py %*
if not errorlevel 1 goto :ok
py -3 tools\bootstrap.py run tools\launch_client.py %*
if not errorlevel 1 goto :ok
py tools\bootstrap.py run tools\launch_client.py %*
if not errorlevel 1 goto :ok
python tools\bootstrap.py run tools\launch_client.py %*
if not errorlevel 1 goto :ok
echo.
echo Python not found.
echo Install Python 3.12+ from https://python.org
pause
:ok
endlocal
