@echo off
rem Launch the live board: background HTML generator + open in browser.
rem Close the minimized console window to stop updating.
cd /d "%~dp0"
start "claude-live-board" /min cmd /c "python live_board.py --html --watch 10"
timeout /t 4 /nobreak >nul
start "" "%~dp0reports\live_board.html"
start "" "%~dp0reports\daily.html"
