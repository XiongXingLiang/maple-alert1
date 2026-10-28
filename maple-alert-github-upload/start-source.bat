@echo off
setlocal
cd /d "%~dp0"
if not exist .build-env\Scripts\pythonw.exe (
  echo Run build.bat first, then open dist\MapleAlert.exe.
  pause
  exit /b 1
)
start "" ".build-env\Scripts\pythonw.exe" "%~dp0app.py"
