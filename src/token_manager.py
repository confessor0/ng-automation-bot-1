import os
import time
import jwt
from dotenv import load_dotenv, set_key, unset_key
from fyers_apiv3 import fyersModel
from src.config import FYERS_APP_ID, FYERS_SECRET_ID, FYERS_REDIRECT_URI


class TokenManager:
    """Manages manually authorized Fyers browser OAuth sessions."""
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        load_dotenv(override=True)
        self.app_id = FYERS_APP_ID or os.getenv("FYERS_APP_ID")
        self.secret_key = FYERS_SECRET_ID or os.getenv("FYERS_SECRET_ID")
        self.redirect_uri = FYERS_REDIRECT_URI or os.getenv("FYERS_REDIRECT_URI")
        self.access_token = os.getenv("FYERS_ACCESS_TOKEN")
        self.refresh_token = os.getenv("FYERS_REFRESH_TOKEN")

    def is_token_valid(self):
        if not self.access_token:
            return False
        try:
            payload = jwt.decode(self.access_token, options={"verify_signature": False})
            return payload.get("exp", 0) > time.time()
        except Exception:
            return False

    def is_token_expiring_soon(self, hours=2):
        if not self.is_token_valid():
            return True
        try:
            exp = jwt.decode(self.access_token, options={"verify_signature": False}).get("exp", 0)
            return exp - time.time() <= hours * 3600
        except Exception:
            return True

    def validate_token(self):
        if not self.is_token_valid():
            return False
        try:
            client = fyersModel.FyersModel(client_id=self.app_id, token=self.access_token, log_path=None)
            return client.get_profile().get("s") == "ok"
        except Exception:
            return False

    def get_login_url(self):
        if not self.app_id or not self.secret_key or not self.redirect_uri:
            return None
        session = fyersModel.SessionModel(client_id=self.app_id, secret_key=self.secret_key,
            redirect_uri=self.redirect_uri, response_type="code", grant_type="authorization_code")
        return session.generate_authcode()

    def generate_token_from_code(self, auth_code):
        try:
            session = fyersModel.SessionModel(client_id=self.app_id, secret_key=self.secret_key,
                redirect_uri=self.redirect_uri, response_type="code", grant_type="authorization_code")
            session.set_token(auth_code)
            response = session.generate_token()
            if response.get("s") != "ok" or not response.get("access_token"):
                return {"success": False, "message": f"Fyers token exchange failed: {response}"}
            self.access_token = response["access_token"]
            self.refresh_token = response.get("refresh_token", "")
            env_path = os.path.join(os.getcwd(), ".env")
            set_key(env_path, "FYERS_ACCESS_TOKEN", self.access_token)
            os.environ["FYERS_ACCESS_TOKEN"] = self.access_token
            if self.refresh_token:
                set_key(env_path, "FYERS_REFRESH_TOKEN", self.refresh_token)
                os.environ["FYERS_REFRESH_TOKEN"] = self.refresh_token
            return {"success": True, "message": "Fyers browser authorization completed."}
        except Exception as exc:
            return {"success": False, "message": str(exc)}

    def clear_tokens(self):
        env_path = os.path.join(os.getcwd(), ".env")
        for key in ("FYERS_ACCESS_TOKEN", "FYERS_REFRESH_TOKEN"):
            unset_key(env_path, key)
            os.environ.pop(key, None)
        self.access_token = None
        self.refresh_token = None
