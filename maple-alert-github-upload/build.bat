@echo off
setlocal
cd /d "%~dp0"
py -3.12 -c "import struct; assert struct.calcsize('P') == 8, 'Use 64-bit Python'"
if errorlevel 1 goto fail
if not exist .build-env\Scripts\python.exe py -3.12 -m venv .build-env
if errorlevel 1 goto fail
.build-env\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto fail
.build-env\Scripts\python.exe -m pip install -r requirements.txt "pyinstaller>=6.11,<7"
if errorlevel 1 goto fail
.build-env\Scripts\python.exe -m unittest discover -v
if errorlevel 1 goto fail
.build-env\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name MapleAlert --add-data "assets;assets" --collect-all windows_toasts --collect-all winrt app.py
if errorlevel 1 goto fail
if not exist dist\MapleAlert.exe goto fail
start "" /wait "dist\MapleAlert.exe" --self-test
if errorlevel 1 goto fail
echo Build complete: dist\MapleAlert.exe
echo Test on Windows before sharing. End users do NOT need Python.
explorer dist
pause
exit /b 0
:fail
echo Build failed. See the error above.
echo This build requires Windows x64 and Python 3.12 x64 with Python Launcher.
pause
exit /b 1
