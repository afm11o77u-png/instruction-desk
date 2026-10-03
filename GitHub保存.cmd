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


echo ========================================
echo  指示書デスク → GitHub 保存
echo ========================================
echo.

git rev-parse --is-inside-work-tree >nul
if errorlevel 1 (
  echo [エラー] Gitの管理情報を読み取れません。上に表示されたエラーをご確認ください。
  pause
  exit /b 1
)

echo 【Git 状態】
git status
echo.

set "HAS_CHANGES="
for /f "delims=" %%A in ('git status --porcelain 2^>nul') do set "HAS_CHANGES=1"
if not defined HAS_CHANGES (
  echo 新しい変更はありません。未送信のCommitを含めて送信を確認します。
  goto push_changes
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
:push_changes
echo GitHub へプッシュしています...
git push -u origin HEAD
if errorlevel 1 (
  echo.
  echo [エラー] GitHub へのプッシュに失敗しました。
  echo ネットワーク接続や認証（GitHub へのログイン）を確認してください。
  echo ローカルのCommitは残っています。接続・認証を確認後、このファイルを再実行してください。
  pause
  exit /b 1
)

echo.
echo GitHubへの保存が完了しました
pause
exit /b 0
