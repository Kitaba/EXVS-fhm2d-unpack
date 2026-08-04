@echo off
powershell -NoProfile -STA -Command "Add-Type -AssemblyName System.Windows.Forms; $m=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5pys5bel5YW35LuF5LuF55So5LqO5aix5LmQ5L+u5pS577yM56aB5q2i6Lez6IS45q2j54mI546p5a6277yB')); $t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5L2/55So5o+Q56S6')); [System.Windows.Forms.MessageBox]::Show($m,$t,'OK','Information') | Out-Null"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

if not exist "%~dp0_runtime\python\python.exe" (
  echo [ERROR] Missing _runtime\python\python.exe
  pause
  exit /b 1
)

"%~dp0_runtime\python\python.exe" -c "import sys, PIL, texture_layout; print('Python:', sys.version.split()[0]); print('Pillow:', PIL.__version__); print('Portable runtime: OK')"
if errorlevel 1 (
  echo [ERROR] Portable runtime check failed.
  pause
  exit /b 1
)

"%~dp0_runtime\python\python.exe" ".\exvs_workflow.py" --help
if errorlevel 1 (
  echo [ERROR] Workflow entry check failed.
  pause
  exit /b 1
)

echo.
echo [OK] No system Python, pip, conda, or extra Python package is required.
pause
