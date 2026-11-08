@echo off
rem More models for Forge Studio (the same menu as during setup). Also:
rem   download-models.bat -Models video
rem   download-models.bat -Models flux3 -HfToken hf_xxx
chcp 65001 >nul
title Forge Studio - Models
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install.ps1" -ModelsOnly %*
pause
