@echo off
title BusTrack Pro - Internet Access via ngrok
echo Run START_LOCAL.bat first, then run this.
echo After ngrok starts, copy the https:// link and share it.
echo.
ngrok http 8080
pause
