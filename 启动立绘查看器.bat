@echo off
powershell -NoProfile -STA -Command "Add-Type -AssemblyName System.Windows.Forms; $m=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5pys5bel5YW35LuF5LuF55So5LqO5aix5LmQ5L+u5pS577yM56aB5q2i6Lez6IS45q2j54mI546p5a6277yB')); $t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5L2/55So5o+Q56S6')); [System.Windows.Forms.MessageBox]::Show($m,$t,'OK','Information') | Out-Null"
setlocal
title EXVSIB Portrait Editor

set "ROOT=%~dp0"
set "PYTHON=%ROOT%_internal\python\python.exe"
set "SERVER=%ROOT%_internal\apps\portrait-editor\server.py"
set "WORKSPACE=%ROOT%workspace"
set "CORE=%ROOT%_internal\core"
set "GAME_ROOT=%ROOT%.."

if not exist "%PYTHON%" (
  echo ERROR: Portable Python is missing.
  echo Path: %PYTHON%
  pause
  exit /b 2
)

if not exist "%WORKSPACE%\asset-mapping\mapping.json" (
  echo INFO: Portrait mapping has not been generated.
  echo Run the unpack tool and choose the full library workflow first.
  pause
  exit /b 2
)

powershell -NoProfile -Command "try { $j=Invoke-RestMethod 'http://127.0.0.1:8765/api/meta' -TimeoutSec 1; if($j.app_id -eq 'exvs_portrait_editor' -and [IO.Path]::GetFullPath($j.workspace) -eq [IO.Path]::GetFullPath('%WORKSPACE%') -and $j.patch_api_version -eq 3 -and $j.frontend_version -eq 'open-folder-v1'){ exit 0 } } catch {}; exit 1" >nul 2>nul
if not errorlevel 1 (
  start "" "http://127.0.0.1:8765/?frontend=open-folder-v1"
  exit /b 0
)

for /L %%P in (17865,1,17874) do (
  powershell -NoProfile -Command "try { $j=Invoke-RestMethod 'http://127.0.0.1:%%P/api/meta' -TimeoutSec 1; if($j.app_id -eq 'exvs_portrait_editor' -and [IO.Path]::GetFullPath($j.workspace) -eq [IO.Path]::GetFullPath('%WORKSPACE%') -and $j.patch_api_version -eq 3 -and $j.frontend_version -eq 'open-folder-v1'){ exit 0 } } catch {}; exit 1" >nul 2>nul
  if not errorlevel 1 (
    start "" "http://127.0.0.1:%%P/?frontend=open-folder-v1"
    exit /b 0
  )
)

"%PYTHON%" "%SERVER%" --workspace "%WORKSPACE%" --core "%CORE%" --game-root "%GAME_ROOT%" --port 17865 --open-browser
set "TOOL_EXIT=%ERRORLEVEL%"
if not "%TOOL_EXIT%"=="0" (
  echo.
  echo Portrait editor exited with code %TOOL_EXIT%.
  pause
)
exit /b %TOOL_EXIT%
