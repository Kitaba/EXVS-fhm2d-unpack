@echo off
powershell -NoProfile -STA -Command "Add-Type -AssemblyName System.Windows.Forms; $m=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5pys5bel5YW35LuF5LuF55So5LqO5aix5LmQ5L+u5pS577yM56aB5q2i6Lez6IS45q2j54mI546p5a6277yB')); $t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5L2/55So5o+Q56S6')); [System.Windows.Forms.MessageBox]::Show($m,$t,'OK','Information') | Out-Null"
setlocal
title EXVSIB Texture Unpack Tool

set "ROOT=%~dp0"
set "PYTHON=%ROOT%_internal\python\python.exe"
set "SERVER=%ROOT%_internal\apps\unpack-tool\server.py"
set "CORE=%ROOT%_internal\core"
set "WORKSPACE=%ROOT%workspace"
set "GAME_ROOT=%ROOT%.."

if not exist "%PYTHON%" (
  echo ERROR: Portable Python is missing.
  echo Path: %PYTHON%
  pause
  exit /b 2
)

dir /b "%GAME_ROOT%\vsac*_Release.exe" >nul 2>nul
if errorlevel 1 (
  echo ERROR: This toolkit folder must be next to a vsac*_Release.exe file.
  echo Game root: %GAME_ROOT%
  pause
  exit /b 2
)

powershell -NoProfile -Command "try { $j=Invoke-RestMethod 'http://127.0.0.1:8766/api/status' -TimeoutSec 1; if($j.app_id -eq 'exvs_unpack_tool' -and [IO.Path]::GetFullPath($j.workspace) -eq [IO.Path]::GetFullPath('%WORKSPACE%') -and $j.ui_api_version -ge 2){ exit 0 } } catch {}; exit 1" >nul 2>nul
if not errorlevel 1 (
  start "" "http://127.0.0.1:8766/"
  exit /b 0
)

for /L %%P in (17875,1,17884) do (
  powershell -NoProfile -Command "try { $j=Invoke-RestMethod 'http://127.0.0.1:%%P/api/status' -TimeoutSec 1; if($j.app_id -eq 'exvs_unpack_tool' -and [IO.Path]::GetFullPath($j.workspace) -eq [IO.Path]::GetFullPath('%WORKSPACE%') -and $j.ui_api_version -ge 2){ exit 0 } } catch {}; exit 1" >nul 2>nul
  if not errorlevel 1 (
    start "" "http://127.0.0.1:%%P/"
    exit /b 0
  )
)

"%PYTHON%" "%SERVER%" --game-root "%GAME_ROOT%" --workspace "%WORKSPACE%" --core "%CORE%" --port 17875 --open-browser
set "TOOL_EXIT=%ERRORLEVEL%"
if not "%TOOL_EXIT%"=="0" (
  echo.
  echo Unpack tool exited with code %TOOL_EXIT%.
  pause
)
exit /b %TOOL_EXIT%
