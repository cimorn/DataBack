@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 (
    echo.
    echo Startup failed. Read the error above and README.md for help.
    pause
    exit /b 1
)
exit /b 0
