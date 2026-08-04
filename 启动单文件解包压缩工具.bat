@echo off
powershell -NoProfile -STA -Command "Add-Type -AssemblyName System.Windows.Forms; $m=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5pys5bel5YW35LuF5LuF55So5LqO5aix5LmQ5L+u5pS577yM56aB5q2i6Lez6IS45q2j54mI546p5a6277yB')); $t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5L2/55So5o+Q56S6')); [System.Windows.Forms.MessageBox]::Show($m,$t,'OK','Information') | Out-Null"
setlocal
title EXVS Single FHM2D Unpack Repack Tool

set "ROOT=%~dp0"
set "PYTHON=%ROOT%_internal\python\python.exe"
set "SERVER=%ROOT%_internal\apps\single-fhm2d-tool\server.py"
set "CORE=%ROOT%_internal\core"
set "WORKSPACE=%ROOT%workspace"

if not exist "%PYTHON%" (
  echo ERROR: Portable Python is missing.
  echo Path: %PYTHON%
  pause
  exit /b 2
)

powershell -NoProfile -Command "try { $j=Invoke-RestMethod 'http://127.0.0.1:8767/api/status' -TimeoutSec 1; if([IO.Path]::GetFullPath($j.workspace) -eq [IO.Path]::GetFullPath('%WORKSPACE%')){ if($j.ui_api_version -ge 3){ exit 0 } else { exit 3 } } } catch {}; exit 1" >nul 2>nul
set "SERVER_STATE=%ERRORLEVEL%"
if "%SERVER_STATE%"=="3" (
  echo INFO: An older single FHM2D tool server is still running.
  echo Close its console window, then launch this file again.
  pause
  exit /b 3
)
if not errorlevel 1 (
  start "" "http://127.0.0.1:8767/"
  exit /b 0
)

for /L %%P in (17885,1,17894) do (
  powershell -NoProfile -Command "try { $j=Invoke-RestMethod 'http://127.0.0.1:%%P/api/status' -TimeoutSec 1; if($j.app_id -eq 'exvs_single_fhm2d_tool' -and [IO.Path]::GetFullPath($j.workspace) -eq [IO.Path]::GetFullPath('%WORKSPACE%') -and $j.ui_api_version -ge 3){ exit 0 } } catch {}; exit 1" >nul 2>nul
  if not errorlevel 1 (
    start "" "http://127.0.0.1:%%P/"
    exit /b 0
  )
)

"%PYTHON%" "%SERVER%" --workspace "%WORKSPACE%" --core "%CORE%" --port 17885 --open-browser
set "TOOL_EXIT=%ERRORLEVEL%"
if not "%TOOL_EXIT%"=="0" (
  echo.
  echo Single FHM2D tool exited with code %TOOL_EXIT%.
  pause
)
exit /b %TOOL_EXIT%
