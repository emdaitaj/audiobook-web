@echo off
rem Telegram bot only, no website.
cd /d "%~dp0"
.venv\Scripts\python.exe bot.py
pause
