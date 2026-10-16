@echo off
chcp 65001 >nul
cd /d "%~dp0"
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

set "CSV=%~1"
if "%CSV%"=="" (
  echo 旧システム CSV（item_report）のフルパスを入力してください。
  set /p CSV=CSV:
)
if "%CSV%"=="" (
  echo CSV が指定されていません。
  pause
  exit /b 1
)

REM Default DB = instruction-desk data\stock.sqlite3 (same as server.py).
REM Does NOT use stock-desk\data\live or sample. Set STOCK_DB to override.
if defined STOCK_DB (
  "%PYEXE%" %PYARGS% -X utf8 compare_item_report.py "%CSV%" --db "%STOCK_DB%" %2 %3 %4 %5
) else (
  "%PYEXE%" %PYARGS% -X utf8 compare_item_report.py "%CSV%" %2 %3 %4 %5
)
set ERR=%ERRORLEVEL%
if %ERR% neq 0 (
  echo 比較に失敗しました。
  pause
  exit /b %ERR%
)
echo.
echo 完了しました。Excel は stock-desk\data\compare\ （または --desktop / --out）です。
pause
exit /b 0
