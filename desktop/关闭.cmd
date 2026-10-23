@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1" %*
if errorlevel 1 (
    echo.
    echo Shutdown failed. Read the error above and README.md for help.
    pause
    exit /b 1
)
exit /b 0
