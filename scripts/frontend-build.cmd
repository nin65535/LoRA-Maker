@echo off
setlocal
pwsh.exe -NoLogo -ExecutionPolicy Bypass -File "%~dp0frontend-build.ps1"
set "BUILD_EXIT=%ERRORLEVEL%"
echo.
if not "%BUILD_EXIT%"=="0" echo Build failed. Exit code: %BUILD_EXIT%
pause
exit /b %BUILD_EXIT%
