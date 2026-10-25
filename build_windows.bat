@echo off
setlocal
cd /d "%~dp0"
py -3.11 --version
if errorlevel 1 goto :error
if not exist ".venv\Scripts\python.exe" py -3.11 -m venv .venv
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m unittest discover -s tests -v
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean SinsWorldLauncher.spec
if errorlevel 1 goto :error
for /f %%V in ('".venv\Scripts\python.exe" -c "from version import VERSION; print(VERSION)"') do set "SW_VERSION=%%V"
".venv\Scripts\python.exe" tools/make_release.py launcher --exe dist/SinsWorldLauncher.exe --version %SW_VERSION% --output dist/launcher-manifest.json
if errorlevel 1 goto :error
echo READY: dist\SinsWorldLauncher.exe and dist\launcher-manifest.json
pause
exit /b 0
:error
echo Build failed. Read messages above.
pause
exit /b 1
