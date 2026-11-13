@echo off
rem Double-click to ring the parent app once.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-call.ps1" %*
pause
