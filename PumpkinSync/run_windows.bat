@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo PumpkinSync has not been set up yet. Running setup...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"
    if errorlevel 1 (
        echo.
        echo Setup did not complete.
        pause
        exit /b 1
    )
)

.venv\Scripts\python.exe -m pumpkinsync %*
if errorlevel 1 pause
