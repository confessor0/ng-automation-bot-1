@echo off
title NG Mini Trading Bot - Restart
color 0E

echo ========================================
echo    RESTARTING NG MINI BOT
echo ========================================
echo.

REM Stop the bot
echo Stopping bot...
call stop_bot.bat

REM Wait 3 seconds
timeout /t 3 /nobreak >nul

REM Start the bot
echo.
echo Starting bot...
call start_bot.bat
