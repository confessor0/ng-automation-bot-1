from fyers_apiv3 import fyersModel
import webbrowser
import sys
from src.config import FYERS_APP_ID, FYERS_SECRET_ID, FYERS_REDIRECT_URI

def generate_token():
    """
    Helps the user generate an access token via the Authorization Code flow.
    """
    print("🚀 FYERS TOKEN GENERATION")
    print("-" * 30)
    
    # 1. Create Session Model
    session = fyersModel.SessionModel(
        client_id=FYERS_APP_ID,
        secret_key=FYERS_SECRET_ID,
        redirect_uri=FYERS_REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code"
    )

    # 2. Generate Auth URL
    auth_url = session.generate_authcode()
    
    if len(sys.argv) > 1:
        # Code provided via CLI
        auth_code = sys.argv[1]
        print(f"Using provided Auth Code: {auth_code[:10]}...")
    else:
        # Interactive Mode
        print("\n1. Opening Authentication URL in browser...")
        print(f"   URL: {auth_url}")
        webbrowser.open(auth_url)

        # 3. Input Auth Code
        print("\n2. Please login, wait for the redirect, and copy the 'auth_code' from the URL.")
        try:
            auth_code = input("👉 Enter the Auth Code here: ").strip()
        except EOFError:
            print("Run with auth_code as argument or provide input.")
            return

    if not auth_code:
        print("❌ No code entered.")
        return

    # 4. Generate Token
    session.set_token(auth_code)
    try:
        response = session.generate_token()
        if response.get("s") == "ok":
            access_token = response["access_token"]
            refresh_token = response.get("refresh_token", "") # Capture Refresh Token
            print(f"\n✅ TOKEN GENERATED SUCCESSFULLY!")
            print(f"Access Token: {access_token[:20]}...[TRUNCATED]")
            if refresh_token:
                print(f"Refresh Token: {refresh_token[:20]}...[TRUNCATED]")
            
            # Save to .env
            update_env_file(access_token, refresh_token)
        else:
            print(f"\n❌ Error generating token: {response}")
    except Exception as e:
        print(f"\n❌ Exception: {e}")

def update_env_file(access_token, refresh_token=""):
    env_path = ".env"
    try:
        with open(env_path, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []
    
    new_lines = []
    keys_found = {"FYERS_ACCESS_TOKEN": False, "FYERS_REFRESH_TOKEN": False}
    
    for line in lines:
        if line.startswith("FYERS_ACCESS_TOKEN="):
            new_lines.append(f"FYERS_ACCESS_TOKEN={access_token}\n")
            keys_found["FYERS_ACCESS_TOKEN"] = True
        elif line.startswith("FYERS_REFRESH_TOKEN="):
            if refresh_token:
                new_lines.append(f"FYERS_REFRESH_TOKEN={refresh_token}\n")
            else:
                 new_lines.append(line) # Keep old if empty
            keys_found["FYERS_REFRESH_TOKEN"] = True
        else:
            new_lines.append(line)
    
    # Append if missing
    if not keys_found["FYERS_ACCESS_TOKEN"]:
        new_lines.append(f"FYERS_ACCESS_TOKEN={access_token}\n")
    if refresh_token and not keys_found["FYERS_REFRESH_TOKEN"]:
        new_lines.append(f"\nFYERS_REFRESH_TOKEN={refresh_token}\n")
    
    with open(env_path, "w") as f:
        f.writelines(new_lines)
    print("✅ Saved tokens to .env file.")

if __name__ == "__main__":
    generate_token()
