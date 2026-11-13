@echo off
rem Double-click to start the check-in backend. Runs start-backend.ps1 without changing the execution policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-backend.ps1" %*
pause
