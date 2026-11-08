@echo off
rem Updates Forge Studio: the newest code from GitHub, then the setup again (it keeps your settings, models and
rem extensions' data, and only adds what changed).
chcp 65001 >nul
title Forge Studio - Update
cd /d "%~dp0"
call "%~dp0installer\env.bat"
git pull --ff-only
if errorlevel 1 (
    echo.
    echo The update could not be pulled. If you changed files of Forge Studio itself, see README, "Updating".
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install.ps1" -Models none %*
pause
