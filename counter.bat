@echo off
rem Agent Counter: rebuild reports\counter.html from the memory ledgers and open it.
rem (ASCII only -- cmd reads this file as cp932 and mangles Japanese comments.)
cd /d "%~dp0"
python counter_gen.py
