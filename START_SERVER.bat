@echo off
title BusTrack Pro v4.0
cd /d "%~dp0"
echo.
echo  Open browser at: http://localhost:8080
echo.
python server.py
if %errorlevel% neq 0 python3 server.py
pause
