import os
import time
import json
import base64
import hmac
import struct
import requests
import jwt  # For decoding token expiry
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv, set_key
from fyers_apiv3 import fyersModel
from src.config import FYERS_APP_ID, FYERS_SECRET_ID, FYERS_REDIRECT_URI

class TokenManager:
    # ===== ROBUST AUTO-LOGIN CONSTANTS =====
    TOKEN_CACHE_FILE = "/tmp/.fyers_token_cache.json" if any(os.environ.get(x) for x in ["VERCEL", "RENDER", "RAILWAY"]) else ".fyers_token_cache.json"
    LOGIN_COOLDOWN_SECONDS = 0  # No cooldown - login immediately when needed
    API_CALL_DELAY = 1  # 1 second between API calls during login (safe but fast)
    TOKEN_REFRESH_HOURS = 4  # Refresh token 4 hours before expiry
    
    # Class-level state for cooldown tracking
    _last_login_attempt = 0
    _last_login_success = 0
    
    # Singleton instance
    _instance = None
    
    @classmethod
    def get_instance(cls):
        """Get singleton instance of TokenManager."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def is_token_valid(self):
        """
        Check if current token is valid based on JWT expiry.
        Does NOT make any API calls - just checks JWT payload.
        Returns True if token exists and has not expired.
        """
        if not self.access_token:
            return False
        try:
            payload = jwt.decode(self.access_token, options={"verify_signature": False})
            exp_timestamp = payload.get("exp", 0)
            return exp_timestamp > time.time()
        except Exception:
            return False
    
    def __init__(self):
        load_dotenv(override=True)
        self.access_token = os.getenv("FYERS_ACCESS_TOKEN")
        self.refresh_token = os.getenv("FYERS_REFRESH_TOKEN")
        self.app_id = FYERS_APP_ID
        self.secret_key = FYERS_SECRET_ID
        self.redirect_uri = FYERS_REDIRECT_URI
        
        # Headless Creds
        self.fyers_id = os.getenv("FYERS_ID")
        self.pin = os.getenv("FYERS_PIN")
        self.totp_key = os.getenv("FYERS_TOTP_KEY")
        
        # Load cached token if available and no token in env
        if not self.access_token:
            self._load_token_cache()
    
    def _load_token_cache(self):
        """Load cached token from disk if available and valid."""
        try:
            if os.path.exists(self.TOKEN_CACHE_FILE):
                with open(self.TOKEN_CACHE_FILE, 'r') as f:
                    cache = json.load(f)
                
                cached_token = cache.get("access_token")
                if cached_token:
                    # Verify token hasn't expired
                    try:
                        payload = jwt.decode(cached_token, options={"verify_signature": False})
                        exp = payload.get("exp", 0)
                        if exp > time.time():
                            self.access_token = cached_token
                            self.refresh_token = cache.get("refresh_token", self.refresh_token)
                            print(f"📦 Loaded valid token from cache (expires in {(exp - time.time())/3600:.1f}h)")
                            
                            # Also update .env for consistency
                            self._update_env_v2(self.access_token, self.refresh_token)
                        else:
                            print("📦 Cached token expired, will refresh")
                    except Exception:
                        pass
        except Exception as e:
            print(f"⚠️ Could not load token cache: {e}")
    
    def _save_token_cache(self):
        """Save current token to disk cache."""
        try:
            cache = {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "last_login_success": time.time(),
                "cached_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            with open(self.TOKEN_CACHE_FILE, 'w') as f:
                json.dump(cache, f, indent=2)
            print("💾 Token cached to disk")
        except Exception as e:
            print(f"⚠️ Could not save token cache: {e}")
    
    @classmethod
    def _can_attempt_login(cls, force_bypass=False):
        """
        Check if enough time has passed since last login attempt (cooldown).
        force_bypass: If True, skip cooldown check (used when token is truly expired)
        """
        if force_bypass:
            print("⚠️ Bypassing cooldown - token is expired or missing")
            return True
            
        elapsed = time.time() - cls._last_login_attempt
        if elapsed < cls.LOGIN_COOLDOWN_SECONDS:
            remaining = cls.LOGIN_COOLDOWN_SECONDS - elapsed
            print(f"⏳ Login cooldown active. Wait {remaining:.0f}s before next attempt.")
            return False
        return True
    
    @classmethod
    def _throttle_api_call(cls):
        """Add delay between API calls to prevent rate limiting."""
        time.sleep(cls.API_CALL_DELAY)


    @staticmethod
    def save_headless_credentials(fyers_id, pin, totp_key, app_id=None, secret_id=None):
        """Saves headless credentials to .env file securely."""
        env_path = ".env"
        if not os.path.exists(env_path):
             with open(env_path, 'w') as f: f.write("")
        
        set_key(env_path, "FYERS_ID", fyers_id)
        set_key(env_path, "FYERS_PIN", pin)
        set_key(env_path, "FYERS_TOTP_KEY", totp_key)
        
        if app_id:
            set_key(env_path, "FYERS_APP_ID", app_id)
        if secret_id:
            set_key(env_path, "FYERS_SECRET_ID", secret_id)
            
        print("✅ Headless Credentials Saved.")

    @staticmethod
    def generate_totp(totp_key, time_step=30, digits=6):
        """Generate TOTP using HMAC-SHA1 (same as pyotp)"""
        key = base64.b32decode(totp_key.upper() + "=" * ((8 - len(totp_key)) % 8))
        counter = struct.pack(">Q", int(time.time() / time_step))
        mac = hmac.new(key, counter, "sha1").digest()
        offset = mac[-1] & 0x0F
        binary = struct.unpack(">L", mac[offset : offset + 4])[0] & 0x7FFFFFFF
        return str(binary)[-digits:].zfill(digits)

    def is_token_expiring_soon(self, hours=2):
        """
        Check if access token expires within specified hours.
        Proactively refreshes before expiry to avoid interruptions.
        """
        if not self.access_token:
            return True
        try:
            # Fyers tokens are JWTs, decode without verification to check expiry
            payload = jwt.decode(self.access_token, options={"verify_signature": False})
            exp_timestamp = payload.get("exp", 0)
            remaining_seconds = exp_timestamp - time.time()
            remaining_hours = remaining_seconds / 3600
            
            if remaining_hours > hours:
                return False
            else:
                print(f"⏰ Token expires in {remaining_hours:.1f} hours (threshold: {hours}h)")
                return True
        except Exception as e:
            print(f"⚠️ Could not decode token expiry: {e}")
            return True  # Assume expiring if we can't decode

    def get_token_diagnostics(self):
        """
        Returns detailed diagnostics about current token state.
        Useful for startup logging and debugging.
        """
        diagnostics = {
            "has_access_token": bool(self.access_token),
            "has_refresh_token": bool(self.refresh_token),
            "has_headless_creds": all([self.fyers_id, self.pin, self.totp_key]),
            "access_token_valid": False,
            "expires_in_hours": None,
            "fyers_id": self.fyers_id[:4] + "***" if self.fyers_id else None
        }
        
        if self.access_token:
            try:
                payload = jwt.decode(self.access_token, options={"verify_signature": False})
                exp_timestamp = payload.get("exp", 0)
                remaining_seconds = exp_timestamp - time.time()
                diagnostics["expires_in_hours"] = round(remaining_seconds / 3600, 2)
                diagnostics["access_token_valid"] = remaining_seconds > 0
            except:
                pass
        
        return diagnostics

    def print_startup_diagnostics(self):
        """Print detailed token status at startup."""
        diag = self.get_token_diagnostics()
        print("\n" + "="*50)
        print("🔐 FYERS AUTHENTICATION STATUS")
        print("="*50)
        
        # Deployment Environment
        env_type = "Local"
        if os.environ.get("RENDER"): env_type = "Render (Cloud)"
        elif os.environ.get("VERCEL"): env_type = "Vercel (Cloud)"
        elif os.environ.get("RAILWAY_STATIC_URL"): env_type = "Railway (Cloud)"
        print(f"  🌐 Environment: {env_type}")
        
        print(f"  📋 Fyers ID: {diag['fyers_id'] or '❌ Not Set'}")
        print(f"  🔑 Access Token: {'✅ Present' if diag['has_access_token'] else '❌ Missing'}")
        print(f"  🔄 Refresh Token: {'✅ Present' if diag['has_refresh_token'] else '❌ Missing'}")
        print(f"  🤖 Headless Creds: {'✅ Complete' if diag['has_headless_creds'] else '❌ Incomplete'}")
        
        if not diag['has_headless_creds']:
            missing = []
            if not self.fyers_id: missing.append("FYERS_ID")
            if not self.pin: missing.append("FYERS_PIN")
            if not self.totp_key: missing.append("FYERS_TOTP_KEY")
            if missing:
                print(f"     ⚠️ Missing: {', '.join(missing)}")
        
        if diag['expires_in_hours'] is not None:
            if diag['expires_in_hours'] > 0:
                print(f"  ⏰ Token Expires In: {diag['expires_in_hours']:.1f} hours")
            else:
                print(f"  ⏰ Token: ❌ EXPIRED")
        print("="*50 + "\n")

    @classmethod
    def perform_headless_login(cls, force_bypass=False):
        """
        Orchestrates the full headless login flow.
        Tries multiple endpoints for each step with rate limiting.
        Includes cooldown to prevent API spam (unless force_bypass=True).
        """
        # ===== COOLDOWN CHECK =====
        if not cls._can_attempt_login(force_bypass=force_bypass):
            return {"success": False, "message": "Login cooldown active. Please wait before retrying."}
        
        # Mark login attempt timestamp
        cls._last_login_attempt = time.time()
        
        tm = cls()
        missing = []
        if not tm.fyers_id: missing.append("FYERS_ID")
        if not tm.pin: missing.append("FYERS_PIN")
        if not tm.totp_key: missing.append("FYERS_TOTP_KEY")
        if not tm.app_id: missing.append("FYERS_APP_ID")
        if not tm.secret_key: missing.append("FYERS_SECRET_ID")
        
        if missing:
            msg = f"Missing Environment Variables: {', '.join(missing)}"
            if os.environ.get("RENDER") or os.environ.get("RAILWAY_STATIC_URL"):
                msg += " (Check your Cloud Dashboard Settings -> Environment)"
            return {"success": False, "message": msg}
            
        print("🚀 Starting Fully Autonomous Login Flow (with rate limiting)...")
        
        # Requests Session
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://login.fyers.in/',
            'Origin': 'https://login.fyers.in',
            'Content-Type': 'application/json'
        }
        s = requests.Session()
        # Initialize session cookies
        try:
            s.get("https://login.fyers.in/", headers=headers, timeout=10)
        except Exception as e:
            print(f"⚠️ Warning: Could not reach login page to init cookies: {e}")

        # Clean credentials
        clean_id = tm.fyers_id.strip("'").strip('"')
        clean_pin = tm.pin.strip("'").strip('"')
        clean_totp = tm.totp_key.strip("'").strip('"')
        clean_app_id = tm.app_id.strip("'").strip('"')
        clean_secret = tm.secret_key.strip("'").strip('"')
        app_id_prefix = clean_app_id.split("-")[0]
        
        def get_base64(text):
            return base64.b64encode(text.encode()).decode()

        # Step 1: Send Login OTP
        url_a = "https://api-t2.fyers.in/vagator/v2/send_login_otp_v2"
        # Fallback URL if needed could be added here
        
        payload_a = {"fy_id": get_base64(clean_id), "app_id": "2"}
        try:
            cls._throttle_api_call()  # Rate limiting
            resp_a = s.post(url_a, json=payload_a, headers=headers, timeout=10).json()
        except Exception as e:
            return {"success": False, "message": f"Step 1 Exception: {e}"}

        request_key = resp_a.get("request_key")
        if not request_key:
             return {"success": False, "message": f"Step 1 Failed: {resp_a}"}
        print("✅ Step 1: Login OTP Sent")
        
        # Step 2: Verify TOTP
        try:
            # We use our own TOTP generator which mirrors pyotp
            otp_val = cls.generate_totp(clean_totp)
        except Exception as e:
             return {"success": False, "message": f"TOTP Generation Error: {e}"}

        url_b = "https://api-t2.fyers.in/vagator/v2/verify_otp"
        payload_b = {"request_key": request_key, "otp": otp_val}
        
        try:
            cls._throttle_api_call()  # Rate limiting
            resp_b = s.post(url_b, json=payload_b, headers=headers, timeout=10).json()
        except Exception as e:
            return {"success": False, "message": f"Step 2 Exception: {e}"}

        request_key_2 = resp_b.get("request_key")
        if not request_key_2:
             return {"success": False, "message": f"Step 2 Failed: {resp_b}"}
        print("✅ Step 2: TOTP Verified")
        
        # Step 3: Verify PIN
        url_c = "https://api-t2.fyers.in/vagator/v2/verify_pin_v2"
        payload_c = {
            "request_key": request_key_2,
            "identity_type": "pin",
            "identifier": get_base64(clean_pin)
        }
        
        try:
             cls._throttle_api_call()  # Rate limiting
             resp_c = s.post(url_c, json=payload_c, headers=headers, timeout=10).json()
        except Exception as e:
             return {"success": False, "message": f"Step 3 Exception: {e}"}
             
        if "data" not in resp_c or "access_token" not in resp_c["data"]:
            return {"success": False, "message": f"Step 3 Failed: {resp_c}"}
        
        intermediate_token = resp_c["data"]["access_token"]
        print("✅ Step 3: PIN Verified")
        
        # Step 4: Get Auth Code
        payload_d = {
            "fyers_id": clean_id,
            "app_id": app_id_prefix,
            "redirect_uri": tm.redirect_uri,
            "appType": "100",
            "code_challenge": "",
            "state": "abcdefg",
            "scope": "",
            "nonce": "",
            "response_type": "code",
            "create_cookie": True
        }
        
        urls_d = [
            # The working endpoint found via research:
            "https://api-t1.fyers.in/api/v3/token",
            # Fallbacks:
            "https://api.fyers.in/api/v3/token",
            "https://api-t1.fyers.in/sdk-login/v3/generate-authcode",
            "https://api.fyers.in/sdk-login/v3/generate-authcode",
            "https://api.fyers.in/api/v2/token", 
        ]
        
        headers_d = headers.copy()
        headers_d["authorization"] = f"Bearer {intermediate_token}"
        headers_d["App-Id"] = clean_app_id
        
        auth_code = None
        for url_d in urls_d:
            try:
                cls._throttle_api_call()  # Rate limiting between endpoint attempts
                resp_d = s.post(url_d, json=payload_d, headers=headers_d, timeout=10)
                
                if resp_d.status_code == 200 or resp_d.status_code == 308:
                    try:
                        res = resp_d.json()
                        
                        # Method 1: Check for 'auth' field in data (new API format)
                        if res.get("s") == "ok" and "data" in res:
                            data = res["data"]
                            if "auth" in data:
                                auth_code = data["auth"]
                                print(f"✅ Step 4: Auth Code Retrieved (via {url_d})")
                                break
                        
                        # Method 2: Check for Url in response (old format)
                        url_with_code = res.get("Url") or res.get("data", {}).get("Url")
                        if url_with_code:
                            parsed = urlparse(url_with_code)
                            qs = parse_qs(parsed.query)
                            auth_code = qs.get('auth_code', [None])[0] or qs.get('code', [None])[0]
                            if auth_code:
                                print(f"✅ Step 4: Auth Code Retrieved from URL")
                                break
                                
                    except Exception as parse_err:
                        pass
            except Exception as e:
                pass

        if not auth_code:
             return {"success": False, "message": "Step 4 Failed: Could not retrieve Auth Code from any endpoint."}

        # Step 5: Exchange Auth Code for Access Token
        try:
            sess = fyersModel.SessionModel(
                client_id=clean_app_id,
                secret_key=clean_secret,
                redirect_uri=tm.redirect_uri,
                response_type="code",
                grant_type="authorization_code"
            )
            sess.set_token(auth_code)
            response = sess.generate_token()
            
            
            if response.get("s") == "ok":
                access_token = response["access_token"]
                refresh_token = response.get("refresh_token", "")
                
                # Save to .env
                from get_token import update_env_file
                update_env_file(access_token, refresh_token)
                
                # Save to cache and mark success
                tm.access_token = access_token
                tm.refresh_token = refresh_token
                tm._save_token_cache()
                cls._last_login_success = time.time()
                
                print("✅ Step 5: Access Token Generated and Saved")
                return {"success": True, "message": "Fully Autonomous Login Successful"}
            else:
                return {"success": False, "message": f"Token Exchange Failed: {response}"}

        except Exception as e:
            return {"success": False, "message": f"Step 5 Exception: {str(e)}"}

    def validate_token(self):
        if not self.access_token: return False
        try:
            fyers = fyersModel.FyersModel(client_id=self.app_id, token=self.access_token, log_path=None)
            resp = fyers.get_profile()
            return resp.get("s") == "ok"
        except: return False

    def refresh_access_token(self):
        if not self.refresh_token: return False
        try:
            from fyers_apiv3.access_token import AccessToken
            session = AccessToken(
                client_id=self.app_id,
                secret_key=self.secret_key,
                redirect_uri=self.redirect_uri,
                grant_type="refresh_token",
                refresh_token=self.refresh_token
            )
            resp = session.generate_token()
            if resp.get("s") == "ok":
                self._update_env(resp["access_token"])
                return True
        except: pass
        return False

    def get_login_url(self):
        if not self.app_id or not self.redirect_uri: return None
        session = fyersModel.SessionModel(
            client_id=self.app_id, secret_key=self.secret_key,
            redirect_uri=self.redirect_uri, response_type="code", grant_type="authorization_code"
        )
        return session.generate_authcode()

    def generate_token_from_code(self, auth_code):
        try:
            session = fyersModel.SessionModel(
                client_id=self.app_id, secret_key=self.secret_key,
                redirect_uri=self.redirect_uri, response_type="code", grant_type="authorization_code"
            )
            session.set_token(auth_code)
            resp = session.generate_token()
            
            if resp.get("s") == "ok":
                self._update_env_v2(resp["access_token"], resp.get("refresh_token", ""))
                return {"success": True, "message": "Token Generated"}
            return {"success": False, "message": f"Fyers Error: {resp}"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _update_env(self, new_token):
        self._update_env_v2(new_token, self.refresh_token)

    def _update_env_v2(self, access_token, refresh_token):
        try:
            from get_token import update_env_file
            update_env_file(access_token, refresh_token)
            # Sync local instance
            self.access_token = access_token
            if refresh_token: self.refresh_token = refresh_token
        except: pass

    def ensure_valid_token(self):
        """
        Ensures a valid access token is available.
        Uses a 3-tier fallback: Validate → Refresh → Headless Login
        Includes proactive refresh and token caching to minimize API calls.
        """
        # Print startup diagnostics
        self.print_startup_diagnostics()
        
        # Quick check: is token valid at all? (no API call)
        token_is_valid = self.is_token_valid()
        token_expiring_soon = self.is_token_expiring_soon(hours=self.TOKEN_REFRESH_HOURS)
        
        # Tier 1: Check if current token is valid AND not expiring soon
        if token_is_valid and not token_expiring_soon:
            print("✅ Access Token is Valid (not expiring soon).")
            return self.access_token
        
        # Always bypass cooldown - we want automatic login without restrictions
        force_bypass = True
        
        # Token is either invalid OR expiring soon - try refresh
        if token_expiring_soon and token_is_valid:
            print(f"⏰ Token expiring within {self.TOKEN_REFRESH_HOURS}h - proactively refreshing...")
        else:
            print("⚠️ Access Token Invalid/Expired - will bypass cooldown if needed.")
        
        # Tier 2: Try refresh token
        if self.refresh_access_token():
            print("🔄 Refreshed Access Token Successfully.")
            self._save_token_cache()  # Cache the refreshed token
            return self.access_token
        
        # Tier 3: Full headless login (bypass cooldown if token is truly expired)
        print(f"🚀 Attempting Fully Autonomous Headless Login (force_bypass={force_bypass})...")
        result = self.perform_headless_login(force_bypass=force_bypass)
        if result["success"]:
            # Reload to get the new token from .env
            load_dotenv(override=True)
            self.access_token = os.getenv("FYERS_ACCESS_TOKEN")
            self.refresh_token = os.getenv("FYERS_REFRESH_TOKEN")
            print("✅ Autonomous Login Successful - Zero Manual Input Required!")
            return self.access_token
            
        print(f"❌ Autonomous Login Failed: {result['message']}")
        print("❌ Manual Login Required - Check your .env credentials.")
        return None

