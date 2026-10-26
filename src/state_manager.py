import json
import os
import threading
from datetime import datetime
import uuid
import secrets
from werkzeug.security import check_password_hash, generate_password_hash

# [NEW] Role Definitions
ROLES = {
    "ADMIN": ["VIEW_DASHBOARD", "MANAGE_USERS", "EXECUTE_BENCHMARK", "MANUAL_SCALE", "EMERGENCY_ACTION", "CHANGE_LIMITS", "OVERRIDE_SIGNAL"],
    "RISK_MANAGER": ["VIEW_DASHBOARD", "EMERGENCY_ACTION"],
    "OPERATOR": ["VIEW_DASHBOARD", "EXECUTE_BENCHMARK"],
    "VIEWER": ["VIEW_DASHBOARD"]
}

# [NEW] Canonical Workflow States (10-Step Flow)
WORKFLOW_STATES = {
    "NO_TRADE": "Waiting for signal",
    "SIGNAL_CONFIRMED": "Trend locked, basket pending",
    "BASKET_READY": "Orders prepared, awaiting confirmation",
    "EXECUTING": "Orders being sent sequentially",
    "POSITIONS_ACTIVE": "All orders filled, positions live",
    "OBSERVATION": "Monitoring for adjustment trigger",
    "ADJUSTMENT_REQUIRED": "Adjustment basket ready",
    "ADJUSTMENT_EXECUTING": "Adjustment orders in progress",
    "EXITING": "Exit basket being executed"
}

# Valid state transitions (state -> allowed next states)
WORKFLOW_TRANSITIONS = {
    "NO_TRADE": ["SIGNAL_CONFIRMED"],
    "SIGNAL_CONFIRMED": ["BASKET_READY", "NO_TRADE"],
    "BASKET_READY": ["EXECUTING", "NO_TRADE"],
    "EXECUTING": ["POSITIONS_ACTIVE", "NO_TRADE"],  # NO_TRADE if all orders fail
    "POSITIONS_ACTIVE": ["OBSERVATION"],
    "OBSERVATION": ["ADJUSTMENT_REQUIRED", "EXITING"],
    "ADJUSTMENT_REQUIRED": ["ADJUSTMENT_EXECUTING", "OBSERVATION"],
    "ADJUSTMENT_EXECUTING": ["OBSERVATION", "EXITING"],
    "EXITING": ["NO_TRADE"]
}

class UserManager:
    def __init__(self, storage_file=None):
        if storage_file is None:
            storage_file = "/tmp/users.json" if os.environ.get("VERCEL") else "users.json"
        self.storage_file = storage_file
        self.sessions = {} # token -> user_id
        self.load_users()

    def load_users(self):
        try:
            if os.path.exists(self.storage_file):
                with open(self.storage_file, 'r') as f:
                    self.users = json.load(f)
            else:
                # Default Admin User
                self.users = {
                    "admin": {
                        "id": "u_admin",
                        "password": "password123", 
                        "name": "System Admin",
                        "role": "ADMIN",
                        "email": "admin@example.com",
                        "active": True,
                        "last_login": None
                    }
                }
                self.save_users()
        except Exception as e:
            print(f"Error loading users: {e}")
            self.users = {}

    def save_users(self):
        try:
            with open(self.storage_file, 'w') as f:
                json.dump(self.users, f, indent=4)
        except Exception as e:
            print(f"Error saving users: {e}")

    def authenticate(self, username, password):
        user = self.users.get(username)
        if user and user.get("active") and self._verify_password(user, password):
            token = str(uuid.uuid4())
            self.sessions[token] = username
            # Migrate legacy plaintext credentials on the first successful login.
            if user.get("password") and not user.get("password_hash"):
                user["password_hash"] = generate_password_hash(str(password))
                user.pop("password", None)
            user["last_login"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.save_users()
            return token, user
        return None, None

    @staticmethod
    def _verify_password(user, password):
        supplied = str(password or "")
        stored_hash = user.get("password_hash")
        if stored_hash:
            try:
                return check_password_hash(stored_hash, supplied)
            except (TypeError, ValueError):
                return False
        # Existing installations may still have a plaintext password. Keep the
        # value readable only for this compatibility check and migrate it after
        # authentication succeeds.
        return secrets.compare_digest(str(user.get("password") or ""), supplied)

    @staticmethod
    def validate_password(password):
        value = str(password or "")
        if len(value) < 8:
            return False, "Password must be at least 8 characters"
        if not any(ch.isalpha() for ch in value) or not any(ch.isdigit() for ch in value):
            return False, "Password must contain at least one letter and one number"
        return True, "Password meets policy"

    def get_user_by_token(self, token):
        username = self.sessions.get(token)
        return self.users.get(username)

    def change_password(self, username, new_password, keep_token=None):
        if username not in self.users:
            return False, "User not found"
        valid, message = self.validate_password(new_password)
        if not valid:
            return False, message
        self.users[username]["password_hash"] = generate_password_hash(str(new_password))
        self.users[username].pop("password", None)
        self.users[username]["must_change_password"] = False
        self.invalidate_sessions(username, keep_token=keep_token)
        self.save_users()
        return True, "Password updated"

    def public_users(self):
        return [dict({"username": username}, **{
            k: v for k, v in user.items() if k not in ("password", "password_hash")
        }) for username, user in self.users.items()]

    def create_or_update_user(self, username, name, email, role, password=None, active=True):
        username = str(username or "").strip().lower()
        if not username or role not in ROLES:
            return False, "Username and a valid role are required"
        if username not in self.users and not password:
            return False, "A new user requires a password"
        if password:
            valid, message = self.validate_password(password)
            if not valid:
                return False, message
        current = self.users.get(username, {"id": "u_" + uuid.uuid4().hex[:10]})
        current.update({"name": str(name or username).strip(), "email": str(email or "").strip(),
                        "role": role, "active": bool(active)})
        if password:
            current["password_hash"] = generate_password_hash(str(password))
            current.pop("password", None)
        current.setdefault("last_login", None)
        self.users[username] = current
        self.save_users()
        return True, "User saved"

    def set_user_active(self, username, active):
        if username not in self.users: return False
        if username == "admin" and not active:
            return False
        self.users[username]["active"] = bool(active)
        if not active:
            self.invalidate_sessions(username)
        self.save_users()
        return True

    def invalidate_sessions(self, username, keep_token=None):
        for token, session_username in list(self.sessions.items()):
            if session_username == username and token != keep_token:
                self.sessions.pop(token, None)

    def reset_password(self, username, new_password=None):
        if username not in self.users:
            return False, "User not found", None
        temporary = str(new_password or (secrets.token_urlsafe(9) + "A1"))
        valid, message = self.validate_password(temporary)
        if not valid:
            return False, message, None
        self.users[username]["password_hash"] = generate_password_hash(temporary)
        self.users[username].pop("password", None)
        self.users[username]["must_change_password"] = True
        self.invalidate_sessions(username)
        self.save_users()
        return True, "Password reset", temporary

    def delete_user(self, username):
        if username not in self.users:
            return False, "User not found"
        if username == "admin":
            return False, "The primary admin account cannot be deleted"
        self.invalidate_sessions(username)
        del self.users[username]
        self.save_users()
        return True, "User deleted"

class StateManager:
    _instance = None
    _lock = threading.Lock() # Class-level lock for singleton creation
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock: # Acquire class-level lock for thread-safe singleton creation
                if cls._instance is None:
                    cls._instance = cls() # Initialize the instance
        return cls._instance

    def __init__(self):
        if StateManager._instance is not None:
            raise Exception("This class is a singleton!")
        
        self.lock = threading.RLock() # Instance-level lock for state modifications
        self.user_manager = UserManager() # [NEW]
        self.logs = [] # [NEW] Internal structured log storage
        
        # Initial State
        self.state = {
            "status": "RUNNING", # IDLE, RUNNING, STOPPED
            "last_update": None,
            "trend": "NEUTRAL",
            "atm_strike": 0,
            "expiry": "-",
            "market_price": 0.0,
            "positions": [],
            "total_pnl": 0.0,
            "logs": [], # This will be a placeholder or for backward compatibility, actual logs are in self.logs
            
            # [NEW] Canonical Workflow State (10-Step Flow)
            "workflow_state": "NO_TRADE",  # See WORKFLOW_STATES for valid values
            "workflow_basket_type": None,  # "ENTRY" | "ADJUSTMENT" | "EXIT"
            
            # [NEW] NG Strategy State (Centralized)
            "ng_strategy_state": {
                "phase": "BENCHMARK",    # BENCHMARK, SCALING, OBSERVATION, EXIT
                "sub_phase": "ACTIVE",   # ACTIVE, LOCKED, WAITING
                "trend_bias": "NEUTRAL", 
                "signal_source": "NONE", # ATM_CE, ATM_PE
                "sustain_active": False,
                "sustain_start_time": None,
                "observation_lock": False,
                "engine_heartbeat": 0    # Timestamp of last logic run
            },

            # NG TREND ENGINE DATA (Visualized on Dashboard)
            "ng_trend_engine": {
                "ce_leg": {"symbol": "-", "price": 0.0, "ma9": 0.0, "status": "NEUTRAL"},
                "pe_leg": {"symbol": "-", "price": 0.0, "ma9": 0.0, "status": "NEUTRAL"},
                "signal_source": "-",
                "confirmation_time": "-"
            },

            # STRATEGY STATE MACHINE
            "strategy_machine": {
                "state": "BENCHMARK", # BENCHMARK, SCALING, OBSERVATION, EXITED
                "level": 5,           # Logical lots (5, 3, 0)
                "status": "ACTIVE"    # ACTIVE, LOCKED, WAITING
            },

            # RISK ADJUSTMENT CONTEXT
            "adjustment_context": {
                "trigger_reason": "No adjustments needed",
                "trigger_source": "-",
                "sustenance_timer": "0.00h / 6.00h",
                "progress_percent": 0
            },

            # OBSERVATION CONTEXT
            "observation_context": {
                "lock_expiry": "-",
                "countdown": "Waiting for scaling",
                "is_locked": False,
                "action_allowed": "Monitor"
            },
            
            # USER PROFILE & RISK LIMITS [NEW]
            "user_profile": {
                "identity": {
                    "name": "Ahmed (Admin)",
                    "role": "Risk Manager",
                    "login_id": "ADM-001",
                    "session_status": "Active"
                },
                "strategy_lock": {
                    "active_strategy": "NG Mini - Benchmark",
                    "is_locked": True,
                    "lock_reason": "Fund Mandate"
                },
                "risk_limits": {
                    "max_futures": 5,
                    "max_adjustments": 1,
                    "overnight_holding": "Hedged Only",
                    "status": "Within Limits"
                },
                "permissions": {
                    "execute_benchmark": True,
                    "auto_scale_down": True,
                    "manual_scale": False,
                    "reverse_trade": False,
                    "override_signal": False
                }
            },

            # ALERT SYSTEM [NEW]
            "alerts": [], # List of {priority, category, message, timestamp}

            # ADMIN CONTROL PANEL [NEW]
            "admin_config": {
                "max_futures_qty": 5,
                "max_adjustments_per_day": 3,
                "sustain_time_hours": 6.0,
                "observation_period_days": 2,
                "max_drawdown_pct": 2.0,       # Deprecated, use max_daily_loss_pct
                "max_daily_loss_pct": 1.5,     # [NEW] Strict Capital Protection Limit
                "risk_lock_enabled": True,
                "news_risk_enabled": False,
                "active_strategy": "AUTO"
            },
            "system_health": {
                "mode": "LIVE", # LIVE / PAPER / MAINTENANCE
                "data_feed_status": "OK",
                "broker_connection": "CONNECTED",
                "last_candle": "-"
            },
            
            # [NEW] REAL-TIME FUND & RISK STATE
            "fund_risk_state": {
                "capital": 500000.0,
                "used_margin": 0.0,
                "available_margin": 500000.0,
                "risk_utilisation_pct": 0.0,
                "risk_status": "SAFE", # SAFE, CAUTION, RISK_LOCK
                "margin_breakdown": {
                    "futures": 0.0,
                    "hedge_options": 0.0,
                    "iron_fly": 0.0
                },
                "mtm_breakdown": {
                    "futures_mtm": 0.0,
                    "hedge_mtm": 0.0,
                    "iron_fly_mtm": 0.0,
                    "net_mtm": 0.0
                },
                "daily_risk": {
                    "max_loss_allowed": 0.0,
                    "current_mtm": 0.0,
                    "remaining_risk": 0.0,
                    "breached": False
                }
            },

            "emergency_controls": {
                "is_halted": False,
                "force_observation": False,
                "force_flat": False
            },

            # [NEW] ORDER EXECUTION STATE (Sequential Order Management)
            "order_execution_state": {
                "basket_id": None,              # Unique ID for this basket (e.g., "BEARISH_20260105_143215")
                "signal_type": None,            # BULLISH | BEARISH | NEUTRAL
                "signal_time": None,            # ISO timestamp when signal was confirmed
                "signal_source": None,          # e.g., "ATM CE 315 < MA9"
                "orders": [],                   # List of order objects (see schema below)
                "progress": 0,                  # Number of orders filled (e.g., 3 out of 6)
                "total": 0,                     # Total orders in basket
                "status": "IDLE",               # IDLE | PENDING | EXECUTING | COMPLETED | ERROR
                "current_order_index": 0,       # Index of order currently being executed
                "error_message": None,          # Error details if status is ERROR
                "started_at": None,             # When execution started
                "completed_at": None            # When execution completed (all filled or error)
            },
            # Order object schema:
            # {
            #     "seq": 1,                    # Sequence number (1-6 for entry)
            #     "type": "Iron Fly Wing",     # Order type for display
            #     "symbol": "MCX:NATGASMINI26JAN340CE",
            #     "side": "BUY",               # BUY | SELL
            #     "qty": 2,                    # Lot count
            #     "status": "PENDING",         # PENDING | QUEUED | EXECUTING | FILLED | REJECTED | CANCELLED | TIMEOUT
            #     "time": None,                # Timestamp when filled (HH:MM:SS)
            #     "order_id": None,            # Fyers order ID (8-digit)
            #     "fill_price": None,          # Actual execution price
            #     "error": None                # Error message if rejected
            # }

            "adjustment_start_time": None, 
            "strategy_phase": "BENCHMARK", 
            "futures_qty": 0, 
            "basket_data": [], 
            "pending_signal": None, 
            "trade_approval": False, 
            "setup_multiplier": 1 
        }

        # Persist the manual event-risk interlock across process restarts.
        self.news_risk_settings_file = os.path.join(os.getcwd(), "news_risk_settings.json")
        try:
            with open(self.news_risk_settings_file, "r", encoding="utf-8") as settings_file:
                saved_settings = json.load(settings_file)
            self.state["admin_config"]["news_risk_enabled"] = bool(saved_settings.get("enabled", False))
        except (OSError, ValueError, TypeError):
            pass

        # Restore email notification preferences and last trend alert across restarts.
        try:
            with open(os.path.join(os.getcwd(), "email_notification_config.json"), "r", encoding="utf-8") as settings_file:
                saved_email_settings = json.load(settings_file)
            self.state["email_notification_config"] = saved_email_settings.get("notifications", {})
        except (OSError, ValueError, TypeError):
            self.state["email_notification_config"] = {}

    def update(self, key, value, symbol=None):
        with self.lock:
            # Update root state
            self.state[key] = value
            self.state["last_update"] = datetime.now().strftime("%H:%M:%S")

            if key == "admin_config" and isinstance(value, dict):
                try:
                    with open(self.news_risk_settings_file, "w", encoding="utf-8") as settings_file:
                        json.dump({"enabled": bool(value.get("news_risk_enabled", False))}, settings_file)
                except OSError as exc:
                    print(f"Could not persist News / Event Risk setting: {exc}")
            
            # Maintain instrument-specific entry for backward compatibility
            if symbol:
                if "instruments" not in self.state:
                    self.state["instruments"] = {}
                if symbol not in self.state["instruments"]:
                    self.state["instruments"][symbol] = {}
                self.state["instruments"][symbol][key] = value
                self.state["instruments"][symbol]["last_update"] = self.state["last_update"]

    def get_state(self, symbol=None):
        with self.lock:
            if symbol and "instruments" in self.state:
                return self.state["instruments"].get(symbol, {}).copy()
            
            state_copy = self.state.copy()
            # Ensure booleans are JSON serializable
            if 'trade_approval' in state_copy:
                state_copy['trade_approval'] = bool(state_copy['trade_approval'])
            
            # For backward compatibility, populate state["logs"] with string versions
            state_copy["logs"] = self.get_logs()
            
            return state_copy

    def add_log(self, message, category="INFO", user="SYSTEM"):
        # [NEW] Structured Logging
        with self.lock:
            timestamp = datetime.now().strftime("%H:%M:%S")
            
            log_entry = {
                "timestamp": timestamp,
                "message": message,
                "category": category,
                "user": user
            }
            
            self.logs.insert(0, log_entry) # Store dict
            
            if len(self.logs) > 100:
                self.logs.pop()

    def get_logs(self): # Helper for UI
        with self.lock:
             # Convert dict logs to strings for frontend display compatibility
             return [f"[{l['timestamp']}] [{l['category']}] {l['message']}" for l in self.logs]

    def add_alert(self, priority, category, message):
        """
        Adds a structured alert to the system.
        Priorities: INFO (Blue), WARNING (Yellow), CRITICAL (Red)
        """
        with self._lock:
            timestamp = datetime.now().strftime("%H:%M:%S")
            alert = {
                "id": len(self.state["alerts"]) + 1,
                "priority": priority, # INFO, WARNING, CRITICAL
                "category": category, # TREND, ADJUSTMENT, OBSERVATION, RISK
                "message": message,
                "timestamp": timestamp,
                "acknowledged": False
            }
            # Insert at top
            self.state["alerts"].insert(0, alert)
            # Keep last 20 alerts
            if len(self.state["alerts"]) > 20:
                self.state["alerts"].pop()
            
            # Auto-log as well
            log_icon = "ℹ️"
            if priority == "WARNING": log_icon = "⚠️"
            if priority == "CRITICAL": log_icon = "🚨"
            
            self.state["logs"].insert(0, f"[{timestamp}] {log_icon} [{category}] {message}")

    def clear_logs(self):
        with self._lock:
            self.state["logs"] = []
            self.state["alerts"] = []
            self.add_log("🧹 Cache/Logs Cleared by User.")

    def transition_workflow_state(self, new_state, basket_type=None):
        """
        Transitions the workflow state with validation.
        Returns (success, message)
        """
        with self.lock:
            current_state = self.state.get("workflow_state", "NO_TRADE")
            
            # Validate transition
            allowed_transitions = WORKFLOW_TRANSITIONS.get(current_state, [])
            if new_state not in allowed_transitions:
                return False, f"Invalid transition: {current_state} → {new_state}"
            
            # Perform transition
            self.state["workflow_state"] = new_state
            if basket_type:
                self.state["workflow_basket_type"] = basket_type
            
            # Log the transition
            state_desc = WORKFLOW_STATES.get(new_state, new_state)
            self.add_log(f"🔄 Workflow: {current_state} → {new_state} ({state_desc})", category="WORKFLOW")
            
            return True, f"Transitioned to {new_state}"

    def get_workflow_state(self):
        """Returns current workflow state and description."""
        with self.lock:
            state = self.state.get("workflow_state", "NO_TRADE")
            return {
                "state": state,
                "description": WORKFLOW_STATES.get(state, "Unknown"),
                "basket_type": self.state.get("workflow_basket_type"),
                "allowed_transitions": WORKFLOW_TRANSITIONS.get(state, [])
            }

