@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Find Python on this PC before updating the application.
set "PYTHON_RUN="
set "PYTHON_CANDIDATE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%PYTHON_CANDIDATE%" call :check_python
if defined PYTHON_RUN goto python_ready
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PYTHON_RUN=py -3"
if defined PYTHON_RUN goto python_ready
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PYTHON_RUN=python"
if defined PYTHON_RUN goto python_ready
echo [エラー] Python 3.10 以降が見つかりません。
echo このPCにPythonをインストールしてから、もう一度実行してください。
pause
exit /b 1
:python_ready

%PYTHON_RUN% -X utf8 "%~dp0stop.py"
pause

exit /b

:check_python
"%PYTHON_CANDIDATE%" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 set PYTHON_RUN="%PYTHON_CANDIDATE%"
exit /b