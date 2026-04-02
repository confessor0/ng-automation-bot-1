@echo off
title NG Mini Trading Bot - Startup
color 0A

echo ========================================
echo    NG MINI AUTOMATION BOT
echo    Starting Production Mode...
echo ========================================
echo.

REM Change to bot directory
cd /d "%~dp0"

REM Check if virtual environment exists
if not exist ".venv\" (
    echo ERROR: Virtual environment not found!
    echo Please run: python -m venv .venv
    pause
    exit /b 1
)

REM Activate virtual environment
call .venv\Scripts\activate.bat

REM Check if .env exists
if not exist ".env" (
    echo WARNING: .env file not found!
    echo Please create .env with your Fyers credentials
    pause
    exit /b 1
)

REM Create logs directory if it doesn't exist
if not exist "logs\" mkdir logs

REM Start the bot
echo.
echo [%date% %time%] Starting bot...
echo.

REM Set encoding to avoid emoji crashes on Windows CMD
set PYTHONUTF8=1

REM Run bot directly without tee
python bot.py

REM If bot crashes, wait before closing
if errorlevel 1 (
    echo.
    echo ========================================
    echo    BOT STOPPED WITH ERROR
    echo ========================================
    pause
)
