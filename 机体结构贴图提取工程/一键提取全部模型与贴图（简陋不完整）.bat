@echo off
powershell -NoProfile -STA -Command "Add-Type -AssemblyName System.Windows.Forms; $m=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5pys5bel5YW35LuF5LuF55So5LqO5aix5LmQ5L+u5pS577yM56aB5q2i6Lez6IS45q2j54mI546p5a6277yB')); $t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5L2/55So5o+Q56S6')); [System.Windows.Forms.MessageBox]::Show($m,$t,'OK','Information') | Out-Null"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
echo ================================================================
echo EXVS experimental bulk model and texture extraction
echo.
echo WARNING: This experimental entry produces incomplete results.
echo Default source: E:\game\ob\25\data\x64\dplcache_release
echo Default output: workspace bulk extraction directory
echo This operation may take a long time and use substantial disk space.
echo ================================================================
choice /C YN /M "Continue"
if errorlevel 2 exit /b 0
"%~dp0_runtime\python\python.exe" ".\extract_all_models_textures.py"
echo.
echo Task finished. Check the output manifest under workspace.
pause
