@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo  指示書デスク 起動
echo ========================================
echo.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 goto :NO_GIT

echo [Git] branch:
git branch --show-current
echo.
echo [Git] status:
git status
echo.

set "HAS_CHANGES="
for /f "delims=" %%A in ('git status --porcelain 2^>nul') do set "HAS_CHANGES=1"
if defined HAS_CHANGES goto :DIRTY_STOP

set "BRANCH="
for /f "delims=" %%b in ('git branch --show-current 2^>nul') do set "BRANCH=%%b"
if /i "%BRANCH%"=="main" goto :DO_PULL

echo Current branch: %BRANCH%
echo Switching to main before pull...
echo.
git checkout main
if errorlevel 1 goto :CHECKOUT_FAIL
echo.

:DO_PULL
echo Pulling origin main (ff-only)...
git pull --ff-only origin main
if errorlevel 1 goto :PULL_FAIL

echo.
echo Git sync OK. Starting app...
echo.
goto :LAUNCH

:NO_GIT
echo [WARNING] This folder is not a Git repository.
echo Git sync is skipped. Starting the app anyway...
echo [警告] Git リポジトリではないため、Git 同期をスキップして起動します。
echo.
goto :LAUNCH

:DIRTY_STOP
echo [WARNING] Uncommitted local changes detected.
echo [警告] 未コミットのローカル変更があるため、起動を中止します。
echo Pull may overwrite local work, so the app will NOT start.
echo.
echo Next steps:
echo   1. Run GitHub保存.cmd to save changes
echo   2. Or clean up local changes, then run 起動.cmd again
echo.
pause
exit /b 1

:CHECKOUT_FAIL
echo.
echo [ERROR] Failed to switch to main.
echo [エラー] main への切り替えに失敗したため、起動を中止します。
echo The app will NOT start.
pause
exit /b 1

:PULL_FAIL
echo.
echo [ERROR] git pull failed.
echo [エラー] git pull に失敗したため、起動を中止します。
echo Check network, auth, and branch state.
echo The app will NOT start.
echo.
echo Note: no reset --hard / force push is used.
pause
exit /b 1

:LAUNCH
"C:\Users\afm11\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -X utf8 "%~dp0launch.py"
if errorlevel 1 pause
