@echo off
setlocal
title EXVSIB Portrait Editor

set "ROOT=%~dp0"
set "PYTHON=%ROOT%_internal\python\python.exe"
set "SERVER=%ROOT%_internal\apps\portrait-editor\server.py"
set "WORKSPACE=%ROOT%workspace"

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

powershell -NoProfile -Command "try { $j=Invoke-RestMethod 'http://127.0.0.1:8765/api/meta' -TimeoutSec 1; if([IO.Path]::GetFullPath($j.workspace) -eq [IO.Path]::GetFullPath('%WORKSPACE%')){ exit 0 } } catch {}; exit 1" >nul 2>nul
if not errorlevel 1 (
  start "" "http://127.0.0.1:8765/"
  exit /b 0
)

"%PYTHON%" "%SERVER%" --workspace "%WORKSPACE%" --port 8765 --open-browser
set "TOOL_EXIT=%ERRORLEVEL%"
if not "%TOOL_EXIT%"=="0" (
  echo.
  echo Portrait editor exited with code %TOOL_EXIT%.
  pause
)
exit /b %TOOL_EXIT%
