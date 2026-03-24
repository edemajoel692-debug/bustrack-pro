@echo off
title BusTrack Pro v4.0 - Jinja Senior Secondary School
color 0A
cls
echo.
echo  ============================================================
echo   BusTrack Pro v4.0 - Uganda Most Advanced Bus System 2026
echo   GPS - Offline - Geofence - ETA - SOS - QR Code - PWA
echo  ============================================================
echo.
echo  [1] Server starting...
echo  [2] Open browser at: http://localhost:8080
echo  [3] Keep this window OPEN
echo.
cd /d "%~dp0"
python server.py
if %errorlevel% neq 0 python3 server.py
pause
