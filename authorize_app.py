"""
One-Time Manual Authorization for New Fyers App
================================================
New Fyers apps require a one-time manual browser authorization to grant permissions.
Run this script, open the URL in browser, login, and copy the auth_code from the redirect URL.
"""
import os
from dotenv import load_dotenv
from fyers_apiv3 import fyersModel

load_dotenv(override=True)

app_id = os.getenv("FYERS_APP_ID", "").strip("'\"")
secret_id = os.getenv("FYERS_SECRET_ID", "").strip("'\"")
redirect_uri = os.getenv("FYERS_REDIRECT_URI", "").strip("'\"")

print("=" * 60)
print("ONE-TIME MANUAL AUTHORIZATION")
print("=" * 60)
print(f"App ID: {app_id}")
print(f"Redirect URI: {redirect_uri}")
print()

# Generate auth URL
session = fyersModel.SessionModel(
    client_id=app_id,
    secret_key=secret_id,
    redirect_uri=redirect_uri,
    response_type="code",
    grant_type="authorization_code"
)

auth_url = session.generate_authcode()
print("STEP 1: Open this URL in your browser:")
print("-" * 60)
print(auth_url)
print("-" * 60)
print()
print("STEP 2: Login with your Fyers credentials")
print("STEP 3: Grant permission to the app")
print("STEP 4: After redirect, copy the 'auth_code' from the URL")
print()
print("The URL will look like:")
print("http://127.0.0.1:5000/callback?s=ok&code=200&auth_code=eyJhbGciOiJ...")
print()

auth_code = input("Paste the auth_code here: ").strip()

if auth_code:
    session.set_token(auth_code)
    response = session.generate_token()

    if response.get("s") == "ok":
        access_token = response["access_token"]
        refresh_token = response.get("refresh_token", "")

        # Save to .env
        from get_token import update_env_file
        update_env_file(access_token, refresh_token)

        print()
        print("=" * 60)
        print("✅ SUCCESS! Tokens saved to .env")
        print("=" * 60)
        print("You can now run the bot. The headless login should work from now on.")
    else:
        print()
        print(f"❌ Token Generation Failed: {response}")
else:
    print("No auth code provided. Exiting.")
