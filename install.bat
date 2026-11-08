@echo off
rem Forge Studio setup. Options are passed on to installer\install.ps1, for example:
rem   install.bat -Yes -Models recommended
chcp 65001 >nul
title Forge Studio - Setup
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install.ps1" %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
    echo.
    echo Setup did not finish. The log is in installer\install.log
)
echo.
echo %* | find /i "-Yes" >nul || pause
exit /b %CODE%
