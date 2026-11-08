@echo off
rem Starts Forge Studio and opens it in its own app window (Edge or Chrome app mode; else the default browser).
rem This console is the server: closing it stops Forge Studio.
chcp 65001 >nul
title Forge Studio - server
cd /d "%~dp0"
call "%~dp0installer\env.bat"
set "URL=http://127.0.0.1:7860/"

if not exist "%~dp0forge\webui\venv\Scripts\python.exe" (
    echo Forge Studio is not installed yet: run install.bat first.
    pause
    exit /b 1
)

set "APP="
for %%P in ("%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" "%LocalAppData%\Google\Chrome\Application\chrome.exe") do (
    if not defined APP if exist "%%~P" set "APP=%%~P"
)

rem already running: only open the window
curl.exe -s -o nul -m 2 "%URL%" >nul 2>&1
if not errorlevel 1 (
    if defined APP (start "" "%APP%" --app=%URL%) else (start "" "%URL%")
    exit /b 0
)

rem Forge opens the window itself as soon as it is ready (webui.py, SD_WEBUI_STARTUP_APP)
if defined APP (
    set "SD_WEBUI_STARTUP_APP=%APP%"
    set "SD_WEBUI_STARTUP_APP_ARGS=--app=%URL% --new-window"
)
cd /d "%~dp0forge\webui"
call webui-user.bat %*
