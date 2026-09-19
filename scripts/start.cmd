@echo off
setlocal
pwsh.exe -NoLogo -ExecutionPolicy Bypass -File "%~dp0start.ps1"
set "START_EXIT=%ERRORLEVEL%"
if not "%START_EXIT%"=="0" (
    echo.
    echo LoRA Maker stopped with exit code: %START_EXIT%
    pause
)
exit /b %START_EXIT%
