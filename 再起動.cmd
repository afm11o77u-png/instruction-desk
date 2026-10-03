@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem GitHub Desktop includes Git, but does not add it to PATH.
git --version >nul 2>&1
if not errorlevel 1 goto git_ready
for /d %%G in ("%LOCALAPPDATA%\GitHubDesktop\app-*") do if exist "%%~fG\resources\app\git\cmd\git.exe" set "PATH=%%~fG\resources\app\git\cmd;%PATH%"
git --version >nul 2>&1
if not errorlevel 1 goto git_ready
if exist "%ProgramFiles%\Git\cmd\git.exe" set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
git --version >nul 2>&1
if not errorlevel 1 goto git_ready
echo [エラー] Gitが見つかりません。GitHub DesktopまたはGitをインストールしてください。
pause
exit /b 1
:git_ready
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


echo ========================================
echo  指示書デスク 再起動（Git 確認）
echo ========================================
echo.

git rev-parse --is-inside-work-tree >nul
if errorlevel 1 (
  echo [エラー] Gitの管理情報を読み取れません。上に表示されたエラーをご確認ください。
  echo 再起動を中止します。
  pause
  exit /b 1
)

echo 【現在のブランチ】
git branch --show-current
echo.
echo 【Git 状態】
git status
echo.

set "HAS_CHANGES="
for /f "delims=" %%A in ('git status --porcelain 2^>nul') do set "HAS_CHANGES=1"
if defined HAS_CHANGES (
  echo [警告] 未コミットのローカル変更があります。
  echo.
  echo GitHub の main から最新を取得すると変更が失われる恐れがあるため、
  echo 再起動を中止します。アプリの停止・再起動は行いません。
  echo.
  echo 対処:
  echo   1. 「GitHub保存.cmd」で変更を保存する
  echo   2. または不要な変更を整理してから、もう一度「再起動.cmd」を実行する
  echo.
  pause
  exit /b 1
)

set "BRANCH="
for /f "delims=" %%b in ('git branch --show-current 2^>nul') do set "BRANCH=%%b"
if /i not "%BRANCH%"=="main" (
  echo 現在のブランチは「%BRANCH%」です。
  echo 再起動前に main へ切り替えてから最新を取得します。
  echo.
  git checkout main
  if errorlevel 1 (
    echo.
    echo [エラー] main ブランチへの切り替えに失敗しました。
    echo 再起動を中止します。アプリの停止・再起動は行いません。
    pause
    exit /b 1
  )
  echo.
)

echo GitHub の main から最新を取得しています...
git pull --ff-only origin main
if errorlevel 1 (
  echo.
  echo [エラー] git pull に失敗しました。
  echo ネットワーク接続・認証・ブランチの状態を確認してください。
  echo 再起動を中止します。アプリの停止・再起動は行いません。
  echo.
  echo ※ 強制リセット（git reset --hard）や force push は行いません。
  pause
  exit /b 1
)

echo.
echo 最新の取得が完了しました。アプリを再起動します...
echo.
%PYTHON_RUN% -X utf8 "%~dp0restart.py"
if errorlevel 1 pause

exit /b

:check_python
"%PYTHON_CANDIDATE%" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 set PYTHON_RUN="%PYTHON_CANDIDATE%"
exit /b