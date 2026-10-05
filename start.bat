@echo off
title Hawaii Studio Bot
cd /d "%~dp0"
:loop
python bot.py
echo.
echo Bot stopped. Restarting in 5 seconds... (close this window to stop)
timeout /t 5 >nul
goto loop
