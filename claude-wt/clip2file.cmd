@echo off
powershell -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0clip2file.ps1"
timeout /t 3 >nul
