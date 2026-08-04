@echo off
powershell -NoProfile -STA -Command "Add-Type -AssemblyName System.Windows.Forms; $m=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5pys5bel5YW35LuF5LuF55So5LqO5aix5LmQ5L+u5pS577yM56aB5q2i6Lez6IS45q2j54mI546p5a6277yB')); $t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5L2/55So5o+Q56S6')); [System.Windows.Forms.MessageBox]::Show($m,$t,'OK','Information') | Out-Null"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

if not exist "%~dp0_runtime\python\python.exe" (
  echo [ERROR] Missing portable runtime: _runtime\python\python.exe
  pause
  exit /b 1
)

echo Starting EXVS local web workflow...
"%~dp0_runtime\python\python.exe" ".\workflow_web.py"
if errorlevel 1 pause
