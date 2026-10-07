@echo off
setlocal
chcp 65001 >nul

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
set "EASYWRITE_EXIT_CODE=%ERRORLEVEL%"

if not "%EASYWRITE_EXIT_CODE%"=="0" (
    echo.
    echo EasyWrite could not start. See the error above.
    echo Press any key to close this window.
    pause >nul
)

exit /b %EASYWRITE_EXIT_CODE%
