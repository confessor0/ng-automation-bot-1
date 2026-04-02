---
description: How to deploy the NG Automation Bot to Railway
---

# Deploying to Railway

This guide outlines the steps to deploy the NG Automation Bot to [Railway.app](https://railway.app/).

## Prerequisites

1.  **Railway Account:** Sign up at [https://railway.app/](https://railway.app/).
2.  **GitHub Repository:** Push your code to a GitHub repository (private or public).
3.  **Fyers Credentials:** Have your App ID, Secret ID, Fyers ID, PIN, and TOTP key ready.

## Steps

### 1. Prepare Codebase (Already Done)
The following files have been configured for Railway:
-   `Procfile`: Instructions to run `python bot.py` as a web service.
-   `runtime.txt`: Specifies Python 3.11.6.
-   `requirements.txt`: Includes Flask, fyers-apiv3, etc.
-   `src/web_server.py`: Updated to listen on the `$PORT` environment variable.

### 2. Connect Railway to GitHub
1.  Go to your Railway Dashboard.
2.  Click **"New Project"** -> **"Deploy from GitHub repo"**.
3.  Select your repository (`NG Automation` or whatever you named it).
4.  Click **"Deploy Now"**.

### 3. Configure Environment Variables
**Crucial Step:** The bot requires environment variables to function. The deployment will likely crash initially until these are set.

1.  Click on your project in Railway.
2.  Go to the **"Variables"** tab.
3.  Add the following variables (Raw Editor recommended):

```env
# Core Trading Config
CAPITAL_LIMIT=100000
DRY_RUN=True  # Set to False for REAL TRADING

# Fyers Credentials (REQUIRED)
FYERS_APP_ID=your_app_id
FYERS_SECRET_ID=your_secret_id
FYERS_ID=your_fyers_id
FYERS_PIN=your_pin
FYERS_TOTP_KEY=your_totp_key

# Telegram (Optional but Recommended)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```
*Note: You can omit `FYERS_ACCESS_TOKEN` as the bot's `TokenManager` will auto-generate it using the Headless Login flow.*

### 4. Verify Deployment
1.  Go to the **"Deployments"** tab.
2.  The deployment should trigger automatically after saving variables.
3.  Click on the deployment to view **Deploy Logs**. Ensure it says `🚀 Web Interface Launching on http://0.0.0.0:xxxx`.
4.  Railway will provide a public URL (e.g., `https://ng-automation-production.up.railway.app`).
5.  Open that URL to access your Dashboard.

### 5. Persistent Storage (Optional)
Railway files are ephemeral (reset on redeploy). The current bot uses in-memory state or local JSON files.
-   **Token Persistence:** The `tokens` folder will be wiped on redeploy. The bot handles this by re-logging in automatically on startup.
-   **State Persistence:** If you need to keep state across deploys, we will need to upgrade to use a database (Redis or Postgres), or use a Railway Volume.
    -   *For now, the bot is designed to recover gracefully on restart.*

## Troubleshooting
-   **"App Crashed"**: Check logs. If `ModuleNotFoundError`, check `requirements.txt`.
-   **"Port Binding"**: Ensure `src/web_server.py` is reading `os.getenv("PORT")`.
-   **"Login Failed"**: Check your `FYERS_` variables carefully. TOTP key must be the alphanumeric secret, not the 6-digit code.
