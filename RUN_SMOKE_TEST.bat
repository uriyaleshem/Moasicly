@echo off
setlocal
cd /d "%~dp0"

echo Running Mosaicly smoke test...
python -m class_balancer --smoke
if errorlevel 1 (
  echo Smoke test failed.
  pause
  exit /b 1
)
echo.
echo Smoke test passed.
pause
