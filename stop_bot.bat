@echo off
title NG Mini Trading Bot - Stop
color 0C

echo ========================================
echo    STOPPING NG MINI BOT
echo ========================================
echo.

REM Kill all Python processes (be careful if running other Python apps)
echo Stopping bot...
taskkill /F /IM python.exe 2>nul

if errorlevel 1 (
    echo No bot process found.
) else (
    echo Bot stopped successfully.
)

echo.
pause
