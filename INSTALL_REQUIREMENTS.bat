@echo off
setlocal
cd /d "%~dp0"

echo Installing Mosaicly dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Installation failed. Check your internet connection or Python installation.
  pause
  exit /b 1
)
echo.
echo Dependencies installed.
pause
