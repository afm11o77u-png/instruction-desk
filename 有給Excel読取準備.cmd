@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "LEAVE_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%LEAVE_PY%" goto runtime
py -3 -m pip install -r "%~dp0requirements-kintai-leave.txt"
if errorlevel 1 goto failed
goto done
:runtime
"%LEAVE_PY%" -m pip install -r "%~dp0requirements-kintai-leave.txt"
if errorlevel 1 goto failed
:done
echo 暗号化Excelの読取準備が完了しました。アプリを再起動してください。
pause
exit /b 0
:failed
echo インストールできませんでした。Pythonとインターネット接続を確認してください。
pause
exit /b 1
