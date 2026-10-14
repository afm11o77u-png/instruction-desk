@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 女性区分ネーム見本の架空氏名を山田花子へ（起動とは別の保守処理）
rem 通常の起動.cmd / 再起動.cmd では画像変換しません。

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
  echo [エラー] Python 3.10 以降が見つかりません。
  pause
  exit /b 1
)

echo 指示書デスク：女性見本の花子保守
echo 環境変数 DESK_RUN_FEMALE_IMAGE_MAINTENANCE=1 で実行します。
echo.
set "DESK_RUN_FEMALE_IMAGE_MAINTENANCE=1"
%PYTHON_RUN% -X utf8 "%~dp0maintain_female_sample_hanako.py"
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
  echo 終了コード %ERR%。data\female-sample-hanako-report.json を確認してください。
  pause
  exit /b %ERR%
)
echo 完了。レポート: data\female-sample-hanako-report.json
pause
exit /b 0
