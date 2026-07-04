@echo off
setlocal
cd /d "%~dp0"

echo Starting Mosaicly / Shibutz Hacham...
echo.

if not defined CLASS_BALANCER_DB (
  set "CLASS_BALANCER_DB=%USERPROFILE%\.class_balancer\class_balancer.sqlite3"
)

python --version >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.11+ and try again.
  pause
  exit /b 1
)

python -m class_balancer
if errorlevel 1 (
  echo.
  echo The app did not start. If PySide6 is missing, run:
  echo python -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)
