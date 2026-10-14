@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Female-sample Hanako maintenance (separate from normal launch).
rem Normal launch/restart cmd files do not convert images.

set "PYTHON_RUN="
set "PYTHON_CANDIDATE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%PYTHON_CANDIDATE%" (
  "%PYTHON_CANDIDATE%" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PYTHON_RUN=%PYTHON_CANDIDATE%"
)
if not defined PYTHON_RUN (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PYTHON_RUN=py -3"
)
if not defined PYTHON_RUN (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PYTHON_RUN=python"
)
if not defined PYTHON_RUN (
  echo [ERROR] Python 3.10+ not found.
  pause
  exit /b 1
)

echo Instruction Desk: female sample Hanako maintenance
echo Running with DESK_RUN_FEMALE_IMAGE_MAINTENANCE=1
echo.
set "DESK_RUN_FEMALE_IMAGE_MAINTENANCE=1"
%PYTHON_RUN% -X utf8 "%~dp0maintain_female_sample_hanako.py"
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
  echo Exit code %ERR%. Check data\female-sample-hanako-report.json
  pause
  exit /b %ERR%
)
echo Done. Report: data\female-sample-hanako-report.json
pause
exit /b 0
