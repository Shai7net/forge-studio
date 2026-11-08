@echo off
rem Forge Studio's launch options. --api is needed by several tabs (Video Studio, Forge Studio, Time Strata...).
rem Other useful options: --port 7870   --listen (open to your network)   --medvram (cards under 8 GB)
set COMMANDLINE_ARGS=--api

call webui.bat %*
