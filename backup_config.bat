@echo off
rem Backup config.local.json to ~/.claude/backups/agent-ops (versioned snapshots).
rem (ASCII only -- cmd reads this file as cp932 and mangles Japanese comments.)
cd /d "%~dp0"
python backup_config.py %*
