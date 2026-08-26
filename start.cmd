@echo off
title File Store Bot
echo Starting File Store Bot...
if not exist venv (
  echo Creating virtual environment...
  py -m venv venv
)
call venv\Scripts\activate
pip install -r requirements.txt
echo.
echo Make sure BOT_TOKEN, ADMIN_ID and BASE_URL are set in this CMD window.
echo.
py bot.py
pause
