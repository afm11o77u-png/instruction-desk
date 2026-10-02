@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo  指示書デスク 再起動（Git 確認）
echo ========================================
echo.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
  echo [エラー] このフォルダは Git リポジトリではありません。
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
"C:\Users\afm11\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -X utf8 "%~dp0restart.py"
if errorlevel 1 pause
