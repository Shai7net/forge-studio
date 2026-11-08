@echo off
rem Runs Forge in its environment (forge\webui\venv, made by install.bat). Use "Forge Studio.bat" to start it.
if exist webui.settings.bat call webui.settings.bat

if not defined VENV_DIR set "VENV_DIR=%~dp0venv"
set SD_WEBUI_RESTART=tmp/restart
set ERROR_REPORTING=FALSE

mkdir tmp 2>NUL

if not exist "%VENV_DIR%\Scripts\Python.exe" (
    echo Forge Studio's Python environment is missing: run install.bat in the Forge Studio folder first.
    pause
    exit /b 1
)
set "PYTHON=%VENV_DIR%\Scripts\Python.exe"

:launch
"%PYTHON%" launch.py %*
if exist tmp/restart goto :launch
pause
exit /b
