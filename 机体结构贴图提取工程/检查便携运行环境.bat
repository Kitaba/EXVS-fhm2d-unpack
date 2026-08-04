@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

if not exist "%~dp0_runtime\python\python.exe" (
  echo [错误] 缺少 _runtime\python\python.exe
  pause
  exit /b 1
)

"%~dp0_runtime\python\python.exe" -c "import sys, PIL, texture_layout; print('Python:', sys.version.split()[0]); print('Pillow:', PIL.__version__); print('核心模块、中文控制台和便携依赖：正常')"
if errorlevel 1 (
  echo [错误] 便携运行环境检查失败。
  pause
  exit /b 1
)

"%~dp0_runtime\python\python.exe" ".\exvs_workflow.py" --help
if errorlevel 1 (
  echo [错误] 工作流入口检查失败。
  pause
  exit /b 1
)

echo.
echo [完成] 用户无需安装 Python、pip、conda 或额外 Python 包。
pause
