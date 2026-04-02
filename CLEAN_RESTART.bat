@echo off
title NG Bot - Clean Restart
color 0E

echo ========================================
echo    CLEAN RESTART - NG MINI BOT
echo ========================================
echo.

echo [1/4] Killing all Python processes...
taskkill /F /IM python.exe 2>nul
if errorlevel 1 (
    echo No Python processes found
) else (
    echo Python processes terminated
)

echo.
echo [2/4] Waiting 2 seconds...
timeout /t 2 /nobreak >nul

echo.
echo [3/4] Starting fresh bot instance...
cd /d "%~dp0"
start "NG Trading Bot" cmd /k "call venv\Scripts\activate.bat && set PYTHONUTF8=1 && python bot.py"

echo.
echo [4/4] Opening dashboard in browser...
timeout /t 3 /nobreak >nul
start http://localhost:5050

echo.
echo ========================================
echo    BOT RESTARTED SUCCESSFULLY!
echo ========================================
echo.
echo Dashboard: http://localhost:5050
echo.
echo IMPORTANT: Press Ctrl+F5 in browser to force refresh!
echo.
pause
