@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo  指示書デスク → GitHub 保存
echo ========================================
echo.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
  echo [エラー] このフォルダは Git リポジトリではありません。
  pause
  exit /b 1
)

echo 【Git 状態】
git status
echo.

set "HAS_CHANGES="
for /f "delims=" %%A in ('git status --porcelain 2^>nul') do set "HAS_CHANGES=1"
if not defined HAS_CHANGES (
  echo 変更はありません
  pause
  exit /b 0
)

echo 変更をステージします（git add .）...
git add .
if errorlevel 1 (
  echo.
  echo [エラー] git add に失敗しました。保存を中止します。
  pause
  exit /b 1
)

echo.
set /p "MSG=コミットメッセージを入力してください: "
if not defined MSG (
  echo.
  echo [エラー] コミットメッセージが空です。保存を中止します。
  pause
  exit /b 1
)
if "%MSG%"=="" (
  echo.
  echo [エラー] コミットメッセージが空です。保存を中止します。
  pause
  exit /b 1
)

echo.
echo コミットしています...
git commit -m "%MSG%"
if errorlevel 1 (
  echo.
  echo [エラー] コミットに失敗しました。保存を中止します。
  pause
  exit /b 1
)

echo.
echo GitHub へプッシュしています...
git push -u origin HEAD
if errorlevel 1 (
  echo.
  echo [エラー] GitHub へのプッシュに失敗しました。
  echo ネットワーク接続や認証（GitHub へのログイン）を確認してください。
  echo ※ force push は行いません。ローカルのコミットは残っています。
  pause
  exit /b 1
)

echo.
echo GitHubへの保存が完了しました
pause
exit /b 0
