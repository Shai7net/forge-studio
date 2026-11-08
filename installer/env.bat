@echo off
rem The tools Forge Studio installed into its own folder come first on PATH, and caches stay on this drive.
set "FS_ROOT=%~dp0.."
for %%I in ("%FS_ROOT%") do set "FS_ROOT=%%~fI"
set "PATH=%FS_ROOT%\system\uv;%FS_ROOT%\system\git\cmd;%FS_ROOT%\system\llama.cpp;%FS_ROOT%\system\ffmpeg;%PATH%"
set "UV_CACHE_DIR=%FS_ROOT%\system\uv-cache"
set "UV_PYTHON_INSTALL_DIR=%FS_ROOT%\system\python"
set "PIP_CACHE_DIR=%FS_ROOT%\system\pip-cache"
if not defined HF_HOME set "HF_HOME=%FS_ROOT%\_cache\hf"
