@echo off
echo Installing ngrok...
choco install ngrok -y

echo.
echo Starting ngrok tunnel...
ngrok http 5050

echo.
echo Your bot is now accessible via the ngrok URL!
echo Share the HTTPS URL to access from anywhere.
pause
