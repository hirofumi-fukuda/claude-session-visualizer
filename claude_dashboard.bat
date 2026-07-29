@echo off
rem Regenerate the static session dashboard and open it in the browser.
cd /d "%~dp0"
python claude_dashboard_gen.py
start "" "%~dp0reports\claude_dashboard.html"
