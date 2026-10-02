@echo off
chcp 65001 >nul
cd /d "%~dp0"
set STOCK_MODE=sample
set STOCK_PORT=8776
set "PYEXE="
set "PYARGS="
py -3 -c "import sys" >nul 2>&1 && set "PYEXE=py" && set "PYARGS=-3"
if not defined PYEXE python -c "import sys" >nul 2>&1 && set "PYEXE=python"
if not defined PYEXE if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "PYEXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not defined PYEXE (
  echo Python 3 が見つかりません。
  pause
  exit /b 1
)
"%PYEXE%" %PYARGS% -X utf8 launch.py
if errorlevel 1 pause
