@echo off
cd /d "%~dp0"
"C:\Users\afm11\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -X utf8 "%~dp0launch.py"
if errorlevel 1 pause
