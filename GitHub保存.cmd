@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo  指示書デスク - GitHub save
echo ========================================
echo.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 goto :NO_GIT

echo [Git] status:
git status
echo.

set "HAS_CHANGES="
for /f "delims=" %%A in ('git status --porcelain 2^>nul') do set "HAS_CHANGES=1"
if not defined HAS_CHANGES goto :NO_CHANGES

echo Staging changes: git add .
git add .
if errorlevel 1 goto :ADD_FAIL

echo.
set /p "MSG=Commit message: "
if not defined MSG goto :EMPTY_MSG
if "%MSG%"=="" goto :EMPTY_MSG

echo.
echo Creating commit...
git commit -m "%MSG%"
if errorlevel 1 goto :COMMIT_FAIL

echo.
echo Pushing to GitHub...
git push -u origin HEAD
if errorlevel 1 goto :PUSH_FAIL

echo.
echo GitHub save completed.
echo GitHubへの保存が完了しました。
pause
exit /b 0

:NO_GIT
echo [ERROR] This folder is not a Git repository.
echo [エラー] Git リポジトリではないため、GitHub 保存はできません。
echo GitHub保存.cmd needs a git clone (with .git). File-copy folders cannot save.
pause
exit /b 1

:NO_CHANGES
echo No changes to save.
echo 変更はありません。
pause
exit /b 0

:ADD_FAIL
echo.
echo [ERROR] git add failed. Save stopped.
echo [エラー] git add に失敗したため、保存を中止します。
pause
exit /b 1

:EMPTY_MSG
echo.
echo [ERROR] Commit message is empty. Save stopped.
echo [エラー] コミットメッセージが空のため、保存を中止します。
pause
exit /b 1

:COMMIT_FAIL
echo.
echo [ERROR] git commit failed. Save stopped.
echo [エラー] コミットに失敗したため、保存を中止します。
pause
exit /b 1

:PUSH_FAIL
echo.
echo [ERROR] git push failed.
echo [エラー] GitHub へのプッシュに失敗しました。
echo Check network/auth. Local commit remains.
echo Note: force push is not used.
pause
exit /b 1
