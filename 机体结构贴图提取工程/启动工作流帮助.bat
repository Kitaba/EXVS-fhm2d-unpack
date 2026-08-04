@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

if not exist "%~dp0_runtime\python\python.exe" (
  echo [错误] 缺少便携 Python：_runtime\python\python.exe
  pause
  exit /b 1
)

echo 正在启动 EXVS 本地网页工作流……
"%~dp0_runtime\python\python.exe" ".\workflow_web.py"
if errorlevel 1 pause
