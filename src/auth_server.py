from flask import Flask, request
from fyers_apiv3 import fyersModel
from src.config import FYERS_APP_ID, FYERS_SECRET_ID, FYERS_REDIRECT_URI
import webbrowser
import threading
import time
import os

app = Flask(__name__)

# Global to store token status
auth_status = {"status": "WAITING", "message": "Waiting for login..."}

def update_env_file(access_token):
    env_path = ".env"
    try:
        with open(env_path, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []
    
    new_lines = []
    token_saved = False
    for line in lines:
        if line.startswith("FYERS_ACCESS_TOKEN="):
            new_lines.append(f"FYERS_ACCESS_TOKEN={access_token}\n")
            token_saved = True
        else:
            new_lines.append(line)
    
    if not token_saved:
        new_lines.append(f"FYERS_ACCESS_TOKEN={access_token}\n")
    
    with open(env_path, "w") as f:
        f.writelines(new_lines)
    print("✅ Access Token saved to .env")

@app.route('/callback')
def callback():
    global auth_status
    auth_code = request.args.get('auth_code')
    
    if not auth_code:
        return "❌ Error: No auth_code received."

    print(f"\n✅ Auth Code Received: {auth_code[:10]}...")
    
    # Generate Session
    session = fyersModel.SessionModel(
        client_id=FYERS_APP_ID,
        secret_key=FYERS_SECRET_ID,
        redirect_uri=FYERS_REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code"
    )
    
    session.set_token(auth_code)
    try:
        response = session.generate_token()
        if response.get("s") == "ok":
            access_token = response["access_token"]
            update_env_file(access_token)
            auth_status = {"status": "SUCCESS", "message": "Token Generated & Saved!"}
            return f"""
            <html>
                <body style='font-family: sans-serif; text-align: center; padding-top: 50px;'>
                    <h1 style='color: green;'>✅ AUTOMATION SETUP SUCCESS</h1>
                    <p>Fyers API is now connected.</br>Access Token has been saved.</p>
                    <p>You can close this window and return to the terminal.</p>
                    <script>window.close();</script>
                </body>
            </html>
            """
        else:
            return f"❌ Fyers Error: {response}"
            
    except Exception as e:
        return f"❌ Exception: {e}"

def open_browser():
    # Wait a sec for server to start
    time.sleep(1.5)
    
    session = fyersModel.SessionModel(
        client_id=FYERS_APP_ID,
        secret_key=FYERS_SECRET_ID,
        redirect_uri=FYERS_REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code"
    )
    
    auth_url = session.generate_authcode()
    print(f"🔗 Opening: {auth_url}")
    webbrowser.open(auth_url)

if __name__ == "__main__":
    print("🚀 Starting Local Auth Server on Port 5000...")
    print("👉 Please log in to Fyers in the browser window/tab that opens.")
    
    # Open browser in a separate thread
    threading.Thread(target=open_browser).start()
    
    # Run server
    app.run(port=5000)
