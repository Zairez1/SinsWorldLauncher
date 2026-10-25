@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" py -3.11 -m venv .venv
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error
".venv\Scripts\python.exe" launcher.py
if errorlevel 1 goto :error
exit /b 0
:error
echo Launcher failed. Python 3.11 and Microsoft Edge WebView2 Runtime are required.
pause
exit /b 1
