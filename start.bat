@echo off
rem Website + Telegram bot in one process, sharing one job queue.
cd /d "%~dp0"
.venv\Scripts\python.exe run.py
pause
