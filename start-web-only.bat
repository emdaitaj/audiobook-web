@echo off
rem Website only, no Telegram bot.
cd /d "%~dp0"
.venv\Scripts\python.exe app.py
pause
