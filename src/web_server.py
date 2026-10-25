import sys
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass # Python < 3.7 fallback

from flask import Flask, jsonify, render_template, request, Response, session, redirect, url_for
import threading
from functools import wraps
from datetime import datetime
from src.state_manager import StateManager
from src.config import TIMEZONE
from colorama import Fore, Style
import os
import secrets

# Fix template path
# Use CWD (Current Working Directory) assumption since we run from root
template_dir = os.path.abspath("templates")
app = Flask(__name__, template_folder='../templates')
app.secret_key = secrets.token_hex(16) # Random key for sessions

# Disable caching for all responses
@app.after_request
def add_header(response):
    """Add headers to prevent caching"""
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

# ===== GLOBAL ERROR HANDLERS =====
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({"status": "error", "message": "Endpoint not found"}), 404
    return redirect(url_for('login'))

@app.errorhandler(500)
def internal_error(e):
    import traceback
    traceback.print_exc()
    if request.path.startswith('/api/'):
        return jsonify({"status": "error", "message": "Internal server error"}), 500
    return redirect(url_for('home'))

state_mgr = StateManager.get_instance()


def record_strategy_event(event_type, symbol, trend, details, expiry=None,
                          atm_strike=None, market_price=None, payload=None):
    """Write an auditable strategy observation without changing FYERS behavior."""
    try:
        from src.database import UnifiedOrderManager
        UnifiedOrderManager.get_instance().record_strategy_event(
            event_type=event_type,
            symbol=symbol,
            trend=trend,
            signal_status=(details or {}).get("signal_status"),
            futures_confirmation=(details or {}).get("futures_confirmation"),
            expiry=expiry,
            atm_strike=atm_strike,
            market_price=market_price,
            payload=payload or details or {},
        )
    except Exception as exc:
        # Persistence must never interrupt a market-data refresh.
        print(f"⚠️ Strategy event persistence failed: {exc}")

# Authentication Config
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "password123"

# [NEW] RBAC Decorator
from src.state_manager import ROLES

def role_required(min_role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if "user_token" not in session:
                return jsonify({"status": "error", "message": "Login Required"}), 401

            state_mgr = StateManager.get_instance()
            user = state_mgr.user_manager.get_user_by_token(session["user_token"])

            if not user:
                return jsonify({"status": "error", "message": "Invalid Session"}), 401

            # Check permissions
            allowed_actions = ROLES.get(user["role"], [])
            # Map simplified role check for now (Admin has all access)
            if user["role"] == "ADMIN":
                pass
            elif user["role"] == "RISK_MANAGER" and min_role not in ["VIEWER", "RISK_MANAGER"]:
                 return jsonify({"status": "error", "message": "Access Denied: Risk Manager Level Required"}), 403
            elif user["role"] == "OPERATOR" and min_role == "ADMIN":
                 return jsonify({"status": "error", "message": "Access Denied: Admin Level Required"}), 403
            elif user["role"] == "VIEWER" and min_role != "VIEWER":
                 return jsonify({"status": "error", "message": "Access Denied: Read-Only User"}), 403

            return f(*args, **kwargs)
        return decorated_function
    return decorator

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_token' not in session:
             return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        state_mgr = StateManager.get_instance()
        token, user = state_mgr.user_manager.authenticate(username, password)

        if token:
            session['user_token'] = token
            session['username'] = username
            session['role'] = user['role']
            state_mgr.add_log(f"Login Successful: {username}", category="AUTH", user=username)

            # Map RBAC Roles to UI Permission flags
            role_perms = ROLES.get(user["role"], [])
            ui_permissions = {
                "execute_benchmark": "EXECUTE_BENCHMARK" in role_perms and "ALLOWED" or "DENIED",
                "manual_scale": "MANUAL_SCALE" in role_perms and "ALLOWED" or "DENIED",
                "emergency_action": "EMERGENCY_ACTION" in role_perms and "ALLOWED" or "DENIED",
                "manage_users": "MANAGE_USERS" in role_perms and "ALLOWED" or "DENIED",
                "change_limits": "CHANGE_LIMITS" in role_perms and "ALLOWED" or "DENIED",
                "override_signal": "OVERRIDE_SIGNAL" in role_perms and "ALLOWED" or "DENIED"
            }

            # Update displayed profile in state for UI
            prof = state_mgr.get_state().get("user_profile", {})
            prof["identity"].update({
                "name": user["name"],
                "role": user["role"],
                "login_id": f"ID-{username.upper()}",
                "email": user.get("email", ""),
                "session_status": "Active"
            })
            prof["permissions"] = ui_permissions # [NEW] Sync permissions
            state_mgr.update("user_profile", prof)

            return redirect(url_for('home'))
        else:
            return render_template('login.html', error="Invalid Credentials")

    return render_template('login.html')


def _mobile_authenticated_user():
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
    from src.mobile_service import get_mobile_user
    return token, get_mobile_user(token, state_mgr.user_manager)


@app.route('/api/mobile/login', methods=['POST'])
def mobile_login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    token, user = state_mgr.user_manager.authenticate(username, password)
    if not token:
        return jsonify({"success": False, "message": "Username or password is incorrect."}), 401
    # The browser session token is process-local; issue a revocable mobile token
    # persisted as a hash so it survives Railway restarts without storing it raw.
    state_mgr.user_manager.sessions.pop(token, None)
    from src.mobile_service import issue_mobile_session
    mobile_token, expires_at = issue_mobile_session(username)
    state_mgr.add_log("Mobile app login successful", category="AUTH", user=username)
    return jsonify({"success": True, "token": mobile_token, "expires_at": expires_at,
                    "user": {"name": user.get("name", username), "role": user.get("role", "VIEWER")}})


@app.route('/api/mobile/logout', methods=['POST'])
def mobile_logout():
    token, user = _mobile_authenticated_user()
    if not user:
        return jsonify({"success": False, "message": "Login required."}), 401
    data = request.get_json(silent=True) or {}
    from src.mobile_service import remove_device, revoke_mobile_session
    fcm_token = str(data.get("fcm_token", "")).strip()
    if fcm_token:
        remove_device(user.get("id", ""), fcm_token)
    revoke_mobile_session(token)
    return jsonify({"success": True})


@app.route('/api/mobile/snapshot', methods=['GET'])
def mobile_snapshot():
    _, user = _mobile_authenticated_user()
    if not user:
        return jsonify({"success": False, "message": "Login required."}), 401
    snapshot = state_mgr.get_state().get("mobile_market_snapshot")
    if not snapshot:
        return jsonify({"success": True, "ready": False,
                        "message": "Open the dashboard and refresh market data to load the first signal."})
    from datetime import datetime, timezone
    try:
        age_seconds = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(snapshot["as_of"])).total_seconds()))
    except (KeyError, TypeError, ValueError):
        age_seconds = None
    return jsonify({"success": True, "ready": True, "snapshot": snapshot,
                    "age_seconds": age_seconds, "is_stale": age_seconds is None or age_seconds > 300})


@app.route('/api/mobile/refresh', methods=['POST'])
def mobile_refresh():
    _, user = _mobile_authenticated_user()
    if not user:
        return jsonify({"success": False, "message": "Login required."}), 401
    # Reuse the dashboard's market-data refresh path; it fetches data and builds
    # a recommendation but does not submit or execute any orders.
    with app.test_request_context('/api/refresh', method='POST', json={"symbol": "NATGASMINI"}):
        return refresh_market_data()


@app.route('/api/mobile/device', methods=['POST', 'DELETE'])
def mobile_device():
    _, user = _mobile_authenticated_user()
    if not user:
        return jsonify({"success": False, "message": "Login required."}), 401
    data = request.get_json(silent=True) or {}
    fcm_token = str(data.get("fcm_token", "")).strip()
    if not fcm_token or len(fcm_token) > 4096:
        return jsonify({"success": False, "message": "A valid FCM device token is required."}), 400
    from src.mobile_service import register_device, remove_device
    if request.method == 'DELETE':
        remove_device(user.get("id", session.get("username", "")), fcm_token)
    else:
        register_device(user.get("id", ""), fcm_token, "android")
    return jsonify({"success": True})

@app.route('/logout')
def logout():
    u = session.get('username', 'Unknown')
    StateManager.get_instance().add_log(f"Logout: {u}", category="AUTH", user=u)
    session.pop('user_token', None)
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/')
@login_required
def home():
    try:
        # print(f"DEBUG: Attempting to render index.html from {app.template_folder}")
        return render_template('index.html')
    except Exception as e:
        import traceback
        return f"<h1>Error Rendering Template</h1><pre>{traceback.format_exc()}</pre>", 500

@app.route('/simulator')
@login_required
def simulator_page():
    """Dedicated decision-support simulator page; it never submits orders."""
    return render_template('simulator.html')

@app.route('/api/state')
def get_state():
    import json
    import time as time_module
    state = state_mgr.get_state()

    # [FIX] Fast broker connection check using JWT decode (no API call)
    # This ensures UI shows accurate status without slowing down requests
    try:
        import jwt
        import os
        from dotenv import load_dotenv
        load_dotenv(override=True)

        access_token = os.getenv("FYERS_ACCESS_TOKEN")

        if access_token:
            try:
                # Decode JWT to check expiry (fast, no network call)
                payload = jwt.decode(access_token, options={"verify_signature": False})
                exp_timestamp = payload.get("exp", 0)
                remaining_hours = (exp_timestamp - time_module.time()) / 3600

                if remaining_hours > 1:
                    broker_status = "CONNECTED"
                elif remaining_hours > 0:
                    broker_status = "EXPIRING"  # Less than 1 hour left
                else:
                    broker_status = "DISCONNECTED"  # Expired
            except Exception:
                broker_status = "DISCONNECTED"
        else:
            broker_status = "DISCONNECTED"

        # Update state with real-time status
        if "system_health" not in state:
            state["system_health"] = {}
        state["system_health"]["broker_connection"] = broker_status
    except Exception as e:
        print(f"⚠️ Error checking broker status: {e}")

    # Convert to JSON-safe format
    return json.dumps(state, default=str), 200, {'Content-Type': 'application/json'}

@app.route('/api/refresh', methods=['POST'])
def refresh_market_data():
    """
    On-demand refresh endpoint.
    Only fetches fresh data from Fyers when user explicitly requests it.
    This saves API quota by not polling continuously.

    Uses a manually authorized Fyers session when the access token is valid.
    """
    import json
    from datetime import datetime
    from src.rate_limiter import throttle
    from src.utils import get_next_expiry_symbol_suffix, get_atm_strike, construct_symbol
    from src.market_data import MarketDataEngine
    from src.strategy import StrategyEngine

    symbol = "NATGASMINI"
    try:
        # Check JSON body first (frontend sends via POST body), then query params
        if request.is_json and request.json and request.json.get('symbol'):
            symbol = request.json['symbol']
        elif request.args.get('symbol'):
            symbol = request.args.get('symbol')
    except Exception:
        pass

    print(f"🔄 Refresh requested for instrument: {symbol}")

    expiry = get_next_expiry_symbol_suffix(symbol=symbol)
    fut_symbol = construct_symbol(symbol, "FUT", expiry)

    try:
        previous_market_price = state_mgr.get_state().get("market_price", 0.0)
        # Get broker instance - this handles all token refresh automatically
        broker = ensure_broker_connected()

        if not broker or not broker.fyers:
            return jsonify({"success": False, "error": "Failed to connect to Fyers - check Admin Panel for login status"})

        # Throttle before API call
        throttle("refresh_ltp")

        # [OPTIMIZED] Use MarketDataEngine with caching to prevent redundant API calls
        market_data = MarketDataEngine(broker, symbol_base=symbol)
        ltp = market_data._get_ltp(fut_symbol)

        if ltp > 0:
            atm_strike = get_atm_strike(ltp, symbol)

            # Update state
            state_mgr.update("market_price", ltp, symbol=symbol)
            state_mgr.update("atm_strike", atm_strike, symbol=symbol)
            state_mgr.update("expiry", expiry, symbol=symbol)

            # Get funds too
            throttle("refresh_funds")
            funds_data = {}
            funds_resp = broker.fyers.funds()
            if funds_resp.get("s") == "ok":
                fund_limits = funds_resp.get("fund_limit", [])
                capital, used = 0.0, 0.0
                for f in fund_limits:
                    title = f.get("title", "")
                    if "Total Balance" in title:
                        capital = f.get("equityAmount", 0) or f.get("commodityAmount", 0)
                    elif "Utilized" in title:
                        used = f.get("equityAmount", 0) or f.get("commodityAmount", 0)

                funds_data = {"capital": capital, "used": used, "available": capital - used}
                state_mgr.update("funds", funds_data, symbol=symbol)

            # === NEW: Calculate Trend Engine Data ===
            trend_data = {}
            try:
                # Reuse market_data instance used for LTP (has cache populated)
                strategy = StrategyEngine(broker, market_data)

                # Analyze trend - this populates ng_trend_engine in state
                prior_trend = state_mgr.get_state().get("trend", "NEUTRAL")
                trend, _, details = strategy.analyze_trend(fut_symbol, expiry)

                trend_data = {
                    "trend": trend,
                    "ce_price": details.get("ce_close", 0),
                    "ce_ma9": details.get("ce_ma9", 0),
                    "pe_price": details.get("pe_close", 0),
                    "pe_ma9": details.get("pe_ma9", 0)
                }
                print(f"✅ Trend Engine Updated: {trend}")
                # Completed daily CE/PE closes are the confirmed trend. During
                # the session, compare live option quotes with those MA9 values
                # separately so a possible reversal can be notified without
                # changing the confirmed trend or basket.
                mail_cfg = state_mgr.get_state().get("email_notification_config", {})
                import time
                from src.notification_service import send_email_notification, load_provider_settings, save_provider_settings

                def queue_trend_email(kind, observed_trend, subject, message, alert_type=None):
                    option = {"BULLISH": "notify_bullish", "BEARISH": "notify_bearish",
                              "NEUTRAL": "notify_neutral"}.get(observed_trend)
                    enabled_key = ("notify_recovery" if alert_type == "RECOVERY" else
                                   "notify_weakening" if alert_type == "WEAKENING" else
                                   "notify_data_quality" if alert_type == "DATA_QUALITY" else
                                   "notify_potential_trends" if kind == "POTENTIAL" else "notify_confirmed_trends")
                    if not (mail_cfg.get("enabled") and mail_cfg.get("notify_trends", True) and
                            mail_cfg.get(enabled_key, True) and option and
                            mail_cfg.get(option, observed_trend != "NEUTRAL")):
                        return
                    pending_key = "pending_potential_event" if kind == "POTENTIAL" else "pending_confirmed_event"
                    delay_seconds = (int(mail_cfg.get("delay_hours", 0)) * 3600 + int(mail_cfg.get("delay_minutes", 0)) * 60) \
                        if mail_cfg.get("delivery_mode") == "custom_delay" else 0
                    event = {"subject": subject, "message": message, "trend": observed_trend,
                             "kind": kind, "alert_type": alert_type,
                             "due_at": time.time() + (delay_seconds or 60)}
                    if delay_seconds:
                        mail_cfg[pending_key] = event
                    else:
                        provider_cfg = load_provider_settings()
                        provider_cfg["recipients"] = mail_cfg.get("recipients") or mail_cfg.get("recipient", "")
                        if send_email_notification(subject, message, provider_cfg["recipients"],
                                                    settings=provider_cfg, alert_type=alert_type):
                            mail_cfg[f"last_notified_{kind.lower()}_trend"] = observed_trend

                def emit_strategy_alert(alert_type, priority, observed_trend, subject, message,
                                       event_type, payload):
                    """Create one deduplicated in-app/journal alert and optionally email it."""
                    candle_key = str(details.get("candle_at") or datetime.now(TIMEZONE).date())[:10]
                    dedupe_key = f"{symbol}|{expiry}|{candle_key}|{alert_type}|{observed_trend}"
                    seen = mail_cfg.setdefault("alert_dedupe", {})
                    if seen.get(dedupe_key):
                        return
                    seen[dedupe_key] = datetime.now(TIMEZONE).isoformat()
                    state_mgr.add_alert(priority, "TREND", message)
                    record_strategy_event(event_type, symbol, trend, details, expiry, atm_strike, ltp,
                                          {**payload, "alert_type": alert_type, "confirmed_trend": trend,
                                           "dedupe_key": dedupe_key})
                    queue_trend_email("POTENTIAL" if alert_type.startswith("PROVISIONAL") or alert_type == "WEAKENING" else "CONFIRMED",
                                      observed_trend, subject, message, alert_type=alert_type)
                    # Bound persisted dedupe state while keeping one alert per candle/type.
                    if len(seen) > 500:
                        for old_key in list(seen)[:len(seen) - 500]:
                            seen.pop(old_key, None)

                previous_confirmed = mail_cfg.get("last_observed_trend", "NEUTRAL")
                if trend in ("BULLISH", "BEARISH", "NEUTRAL") and trend != previous_confirmed:
                    mail_cfg["last_observed_trend"] = trend
                    emit_strategy_alert("CONFIRMED_TREND_CHANGE", "CRITICAL", trend,
                                        f"NG confirmed trend: {previous_confirmed} → {trend}",
                                        f"CONFIRMED TREND CHANGE: {previous_confirmed} → {trend}.\n"
                                        f"CE close/MA9: {trend_data['ce_price']}/{trend_data['ce_ma9']}\n"
                                        f"PE close/MA9: {trend_data['pe_price']}/{trend_data['pe_ma9']}\n"
                                        "Review the active basket; the new strategy basket is available.",
                                        "CONFIRMED_TREND_CHANGE",
                                        {"previous_trend": previous_confirmed, "confirmation": "COMPLETED_DAILY_CANDLE"})

                live_trend = None
                if details.get("ce_symbol") and details.get("pe_symbol") and details.get("ce_ma9") and details.get("pe_ma9"):
                    live_quotes = market_data._get_batch_ltp([details["ce_symbol"], details["pe_symbol"]])
                    live_ce = float(live_quotes.get(details["ce_symbol"], 0) or 0)
                    live_pe = float(live_quotes.get(details["pe_symbol"], 0) or 0)
                    if live_ce < details["ce_ma9"] and live_pe > details["pe_ma9"]:
                        live_trend = "BEARISH"
                    elif live_ce > details["ce_ma9"] and live_pe < details["pe_ma9"]:
                        live_trend = "BULLISH"
                    elif live_ce < details["ce_ma9"] and live_pe < details["pe_ma9"]:
                        live_trend = "NEUTRAL"
                    if live_ce <= 0 or live_pe <= 0:
                        emit_strategy_alert("DATA_QUALITY", "WARNING", trend,
                                            "NG option quote data unavailable",
                                            "Option quote data is missing or stale. Trend and basket decisions are paused until CE and PE prices are available.",
                                            "DATA_QUALITY_ALERT", {"live_ce": live_ce, "live_pe": live_pe})
                    if live_trend:
                        previous_live = mail_cfg.get("last_observed_live_trend", trend)
                        if live_trend != previous_live:
                            mail_cfg["last_observed_live_trend"] = live_trend
                            if live_trend == trend:
                                alert_type, priority = "RECOVERY", "INFO"
                                title = f"NG {trend} alignment recovered"
                                body = (f"{trend} alignment recovered after an intraday warning.\n"
                                        f"Confirmed trend remains {trend}. No basket switch is required.")
                            elif live_trend == "NEUTRAL":
                                alert_type, priority = "PROVISIONAL_NEUTRAL", "WARNING"
                                title = "NG possible neutral shift"
                                body = (f"POSSIBLE NEUTRAL SHIFT while confirmed trend is {trend}.\n"
                                        "Both CE and PE are below MA9 intraday. This is NOT CONFIRMED until the daily candle closes.")
                            else:
                                alert_type, priority = "PROVISIONAL_REVERSAL", "WARNING"
                                title = f"NG possible {live_trend.lower()} reversal"
                                body = (f"POSSIBLE {live_trend} REVERSAL while confirmed trend is {trend}.\n"
                                        "Do not switch baskets until the completed daily candle confirms the opposite pair.")
                            message = (body + f"\nLive CE: {live_ce:.2f} vs MA9 {details['ce_ma9']:.2f}\n"
                                       f"Live PE: {live_pe:.2f} vs MA9 {details['pe_ma9']:.2f}\n"
                                       "Action: review risk and monitor the daily close.")
                            emit_strategy_alert(alert_type, priority, live_trend, title, message,
                                                "INTRADAY_TREND_CHANGE", {"potential_trend": live_trend,
                                                "confirmation": "PENDING_DAILY_CLOSE", "live_ce": live_ce, "live_pe": live_pe})
                    elif live_ce > 0 and live_pe > 0 and live_ce >= details['ce_ma9'] and live_pe >= details['pe_ma9']:
                        # One side has lost alignment, but the opposite paired
                        # condition is not complete. This is a weakening watch.
                        previous_live = mail_cfg.get("last_observed_live_trend", trend)
                        if previous_live == trend:
                            emit_strategy_alert("WEAKENING", "WARNING", trend,
                                                f"NG {trend} position weakening",
                                                f"One option has lost the confirmed {trend} MA9 alignment intraday.\n"
                                                f"Live CE: {live_ce:.2f} vs MA9 {details['ce_ma9']:.2f}\n"
                                                f"Live PE: {live_pe:.2f} vs MA9 {details['pe_ma9']:.2f}\n"
                                                "The trend is not reversed until the completed daily candle closes.",
                                                "INTRADAY_TREND_WEAKENING", {"live_ce": live_ce, "live_pe": live_pe})
                state_mgr.update("email_notification_config", mail_cfg)
                provider_settings = load_provider_settings()
                provider_settings["notifications"] = mail_cfg
                save_provider_settings(provider_settings)

                # [NEW] Helper for Scenario Analysis (now uses instrument config)
                from src.instrument_config import get_instrument_config, get_hedge_distance, get_wing_distance, get_scenario_moves
                inst_config = get_instrument_config(symbol)

                def calculate_scenario_pnl(trend, ltp, atm_strike):
                    """Simulates P&L for market moves based on instrument-specific percentages."""
                    scenarios = []
                    scenario_moves = get_scenario_moves(symbol, ltp)

                    # Position Params from instrument config
                    lot_sz = inst_config["lot_size"]
                    fut_q = inst_config["fut_lots"]
                    hedge_q = inst_config["hedge_lots"]
                    fly_q = inst_config["fly_lots"]

                    # Dynamic distances
                    h_dist = get_hedge_distance(symbol, ltp)
                    w_dist = get_wing_distance(symbol, ltp)

                    # Entry Estimates
                    entry_price = ltp
                    hedge_premium = ltp * 0.02  # Approx cost for ITM hedge (~2% of CMP)

                    # Iron Fly Payoff Function (Heuristic)
                    def get_fly_pnl(target, center):
                        dist = abs(target - center)
                        max_profit = ltp * 0.012 * fly_q * lot_sz  # ~1.2% of CMP
                        if dist > w_dist * 0.6: return -max_profit * 0.5  # Max loss
                        if dist <= 0: return max_profit  # Max profit
                        return max_profit - (dist / (w_dist * 0.6)) * max_profit * 1.5  # Linear decay

                    for move in scenario_moves:
                        target_price = ltp + move

                        # 1. Futures P&L
                        if trend == "BULLISH":
                            fut_pnl = (target_price - entry_price) * fut_q * lot_sz
                        else: # BEARISH
                            fut_pnl = (entry_price - target_price) * fut_q * lot_sz

                        # 2. Hedge P&L (Intrinsic value at target - Premium)
                        hedge_pnl = 0
                        if trend == "BULLISH":
                            strike = atm_strike + h_dist  # Long Put
                            intrinsic = max(0, strike - target_price)
                            hedge_pnl = (intrinsic - hedge_premium) * hedge_q * lot_sz
                        else:
                            strike = atm_strike - h_dist  # Long Call
                            intrinsic = max(0, target_price - strike)
                            hedge_pnl = (intrinsic - hedge_premium) * hedge_q * lot_sz

                        # 3. Iron Fly P&L
                        fly_pnl = get_fly_pnl(target_price, atm_strike)

                        total_pnl = fut_pnl + hedge_pnl + fly_pnl

                        scenarios.append({
                            "move": move,
                            "target_price": round(target_price, 2),
                            "pnl": round(total_pnl, 2),
                            "is_profit": total_pnl >= 0
                        })
                    return scenarios

                # [NEW] Build Recommended Position Basket based on fresh trend and LTP
                rec_position = None

                # P&L Estimation Parameters from instrument config
                lot_size = inst_config["lot_size"]
                fut_qty = inst_config["fut_lots"]
                hedge_qty = inst_config["hedge_lots"]
                fly_qty = inst_config["fly_lots"]

                # Dynamic distances based on CMP × percentage
                hedge_dist = get_hedge_distance(symbol, ltp)
                wing_dist = get_wing_distance(symbol, ltp)

                if trend in ["BULLISH", "BEARISH"]:
                    from src.utils import construct_symbol

                    # 1. Define Leg Specs (Role, Instrument, Action, Strike, Qty)
                    legs_specs = []
                    if trend == "BULLISH":
                        legs_specs = [
                            {"role": "Primary Trend", "type": "FUT", "strike": 0, "opt_type": None, "action": "BUY", "qty": fut_qty},
                            {"role": "Downside Hedge", "type": "OPT", "strike": atm_strike + hedge_dist, "opt_type": "PE", "action": "BUY", "qty": hedge_qty},
                            {"role": "Iron Fly Straddle", "type": "OPT", "strike": atm_strike, "opt_type": "CE", "action": "SELL", "qty": fly_qty},
                            {"role": "Iron Fly Straddle", "type": "OPT", "strike": atm_strike, "opt_type": "PE", "action": "SELL", "qty": fly_qty},
                            {"role": "Iron Fly Wing", "type": "OPT", "strike": atm_strike + wing_dist, "opt_type": "CE", "action": "BUY", "qty": fly_qty},
                            {"role": "Iron Fly Wing", "type": "OPT", "strike": atm_strike - wing_dist, "opt_type": "PE", "action": "BUY", "qty": fly_qty}
                        ]

                        # Strategy Details for UI
                        primary_action = "BUY FUTURES"
                        primary_detail = f"Go LONG on {inst_config['display_name']} Futures at ₹{ltp:.2f}"
                        hedge_action = "BUY ITM PUT"
                        hedge_detail = f"Buy ITM Put at strike {atm_strike + hedge_dist} for downside protection"
                        rationale = f"PE < MA9 (Bullish) indicated by Daily Trend Engine. Position aims to profit from upward price movement while hedged against downside."
                        risk_profile = "HEDGED - Max loss capped by ITM Put + Iron Fly wings"

                    else: # BEARISH
                        legs_specs = [
                            {"role": "Primary Trend", "type": "FUT", "strike": 0, "opt_type": None, "action": "SELL", "qty": fut_qty},
                            {"role": "Upside Hedge", "type": "OPT", "strike": atm_strike - hedge_dist, "opt_type": "CE", "action": "BUY", "qty": hedge_qty},
                            {"role": "Iron Fly Straddle", "type": "OPT", "strike": atm_strike, "opt_type": "CE", "action": "SELL", "qty": fly_qty},
                            {"role": "Iron Fly Straddle", "type": "OPT", "strike": atm_strike, "opt_type": "PE", "action": "SELL", "qty": fly_qty},
                            {"role": "Iron Fly Wing", "type": "OPT", "strike": atm_strike + wing_dist, "opt_type": "CE", "action": "BUY", "qty": fly_qty},
                            {"role": "Iron Fly Wing", "type": "OPT", "strike": atm_strike - wing_dist, "opt_type": "PE", "action": "BUY", "qty": fly_qty}
                        ]

                        # Strategy Details for UI
                        primary_action = "SELL FUTURES"
                        primary_detail = f"Go SHORT on {inst_config['display_name']} Futures at ₹{ltp:.2f}"
                        hedge_action = "BUY ITM CALL"
                        hedge_detail = f"Buy ITM Call at strike {atm_strike - hedge_dist} for upside protection"
                        rationale = f"CE < MA9 (Bearish) indicated by Daily Trend Engine. Position aims to profit from downward price movement while hedged against upside."
                        risk_profile = "HEDGED - Max loss capped by ITM Call + Iron Fly wings"

                    # 2. Construct Symbols & Fetch Data
                    symbols_map = {}
                    symbols_list = []

                    for leg in legs_specs:
                        sym = construct_symbol(symbol, leg["type"], expiry, leg["strike"], leg["opt_type"])
                        leg["symbol"] = sym
                        symbols_map[sym] = 0.0 # Default
                        if sym:
                            symbols_list.append(sym)

                    # Batch Fetch from Fyers (Optimized with Cache)
                    if symbols_list:
                        try:
                            # Use shared market_data instance to hit cache for FUT/CE/PE
                            fetched = market_data._get_batch_ltp(symbols_list)
                            symbols_map.update(fetched)
                        except Exception as e:
                            print(f"Error fetching quotes for legs: {e}")

                    # 3. Build Detailed Composition List
                    composition = []
                    current_pnl = 0.0 # Estimate start P&L (usually spread loss)

                    for leg in legs_specs:
                        leg_ltp = symbols_map.get(leg["symbol"], 0.0)

                        # Fallback for FUT if 0 (use main ltp)
                        if leg["type"] == "FUT" and leg_ltp <= 0:
                            leg_ltp = ltp

                        # [HARDENING] If Option LTP is 0, try to estimate roughly from intrinsic?
                        # For now, just keep 0 but ensure no crash.

                        # Calculate Approx Value (Not margin, but contract value)
                        # Ensure all variables are floats to prevent TypeErrors
                        try:
                            value = float(leg_ltp) * float(leg["qty"]) * float(lot_size)
                        except:
                            value = 0.0

                        composition.append({
                            "role": leg["role"],
                            "instrument": leg["type"],
                            "action": leg["action"],
                            "strike": leg["strike"] if leg["type"] == "OPT" else "MKT",
                            "qty": leg["qty"],
                            "trading_symbol": leg["symbol"],
                            "ltp": leg_ltp,
                            "value": value
                        })

                    # ════════════════════════════════════════════════════════════
                    # BENCHMARK STRATEGY PAYOFF METRICS (STEPS 4.1 - 4.7)
                    # ════════════════════════════════════════════════════════════

                    # 4.1 LEG-WISE PREMIUM SUMMARY
                    total_premium_received = 0.0
                    total_premium_paid = 0.0
                    futures_entry_value = 0.0

                    for leg in composition:
                        premium = leg["ltp"] * leg["qty"] * lot_size

                        if leg["instrument"] == "FUT":
                            futures_entry_value = leg["ltp"]
                            leg["premium_flow"] = 0  # Futures don't have premium
                            leg["exposure"] = "LONG" if leg["action"] == "BUY" else "SHORT"
                        elif leg["action"] == "SELL":
                            total_premium_received += premium
                            leg["premium_flow"] = premium
                            leg["exposure"] = "SHORT"
                        else:  # BUY
                            total_premium_paid += premium
                            leg["premium_flow"] = -premium
                            leg["exposure"] = "LONG"

                    # 4.2 NET CREDIT / DEBIT
                    net_premium = total_premium_received - total_premium_paid
                    net_premium_type = "CREDIT" if net_premium >= 0 else "DEBIT"

                    # 4.3 MAX PROFIT CALCULATION
                    # Iron Fly Max Profit = Net Premium from straddle - wing cost
                    # Futures Profit = Assumed favorable move = hedge_dist worth of points
                    # Hedge Cost = Premium paid for hedge option

                    hedge_cost = 0.0
                    iron_fly_credit = 0.0
                    wing_cost = 0.0

                    for leg in composition:
                        if "Hedge" in leg["role"]:
                            hedge_cost = abs(leg.get("premium_flow", 0))
                        elif "Straddle" in leg["role"]:
                            iron_fly_credit += leg.get("premium_flow", 0)
                        elif "Wing" in leg["role"]:
                            wing_cost += abs(leg.get("premium_flow", 0))

                    iron_fly_max_profit = iron_fly_credit  # At expiry if price = ATM
                    futures_max_profit = hedge_dist * fut_qty * lot_size  # Favorable move up to hedge distance
                    max_profit = iron_fly_max_profit + futures_max_profit - hedge_cost
                    max_profit_zone = f"Price at ATM ({atm_strike}) + {hedge_dist} pts in trend direction"

                    # 4.4 MAX LOSS CALCULATION
                    # Worst case: Futures move against trend by hedge_dist (hedge activates)
                    hedge_strike = atm_strike + hedge_dist if trend == "BULLISH" else atm_strike - hedge_dist
                    futures_loss_till_hedge = hedge_dist * fut_qty * lot_size  # Loss until hedge kicks in
                    hedge_protection_gain = hedge_dist * hedge_qty * lot_size  # Intrinsic gain from hedge

                    # Iron Fly Max Loss = Wing width - Net Credit (if price moves beyond wings)
                    iron_fly_max_loss = (wing_dist * fly_qty * lot_size) - iron_fly_credit + wing_cost

                    max_loss = futures_loss_till_hedge - hedge_protection_gain + iron_fly_max_loss
                    loss_capped = True
                    naked_exposure = False

                    # 4.5 BREAK-EVEN CALCULATION
                    net_cost_per_unit = (hedge_cost + wing_cost - iron_fly_credit) / (fut_qty * lot_size) if (fut_qty * lot_size) > 0 else 0

                    if trend == "BULLISH":
                        upper_breakeven = ltp + net_cost_per_unit
                        lower_breakeven = hedge_strike  # Protected below this
                    else:
                        upper_breakeven = hedge_strike  # Protected above this
                        lower_breakeven = ltp - net_cost_per_unit

                    # 4.6 RISK-REWARD RATIO
                    risk_reward = abs(max_profit / max_loss) if max_loss != 0 else 0
                    if risk_reward >= 2:
                        trade_classification = "CONSERVATIVE"
                    elif risk_reward >= 1:
                        trade_classification = "BALANCED"
                    else:
                        trade_classification = "AGGRESSIVE"

                    # 4.7 VALIDATION CHECKLIST
                    risk_limit = 50000  # Max acceptable loss
                    validation = {
                        "max_loss_within_limit": abs(max_loss) < risk_limit,
                        "payoff_asymmetry_acceptable": risk_reward >= 0.5,
                        "iron_fly_absorbs_hedge": iron_fly_credit >= hedge_cost * 0.5,
                        "benchmark_compliant": True  # Following exact strategy
                    }
                    trade_approved = all(validation.values())

                    # ════════════════════════════════════════════════════════════
                    # STEP 5: EXPANDED SCENARIO SIMULATOR (Leg-wise P&L)
                    # ════════════════════════════════════════════════════════════
                    def calculate_detailed_scenario(move, trend, ltp, atm_strike, hedge_strike, composition):
                        target_price = ltp + move

                        # Futures P&L
                        if trend == "BULLISH":
                            fut_pnl = move * fut_qty * lot_size
                        else:
                            fut_pnl = -move * fut_qty * lot_size

                        # Hedge P&L (Intrinsic at expiry - Premium)
                        if trend == "BULLISH":
                            # Long PUT at hedge_strike
                            intrinsic = max(0, hedge_strike - target_price)
                        else:
                            # Long CALL at hedge_strike
                            intrinsic = max(0, target_price - hedge_strike)
                        hedge_pnl = (intrinsic * hedge_qty * lot_size) - hedge_cost

                        # Iron Fly P&L
                        dist_from_atm = abs(target_price - atm_strike)
                        if dist_from_atm <= 0:
                            fly_pnl = iron_fly_credit
                        elif dist_from_atm >= wing_dist:
                            fly_pnl = iron_fly_credit - (wing_dist * fly_qty * lot_size)
                        else:
                            fly_pnl = iron_fly_credit - (dist_from_atm * fly_qty * lot_size)

                        total_pnl = fut_pnl + hedge_pnl + fly_pnl

                        return {
                            "move": move,
                            "target_price": round(target_price, 2),
                            "futures_pnl": round(fut_pnl, 2),
                            "hedge_pnl": round(hedge_pnl, 2),
                            "iron_fly_pnl": round(fly_pnl, 2),
                            "net_pnl": round(total_pnl, 2),
                            "is_profit": total_pnl >= 0
                        }

                    scenario_moves = get_scenario_moves(symbol, ltp)
                    scenario_analysis = [
                        calculate_detailed_scenario(m, trend, ltp, atm_strike, hedge_strike, composition)
                        for m in scenario_moves
                    ]

                    # ════════════════════════════════════════════════════════════
                    # BUILD RECOMMENDED POSITION OBJECT
                    # ════════════════════════════════════════════════════════════
                    rec_position = {
                        "direction": trend,
                        "primary_action": primary_action,
                        "primary_action_detail": primary_detail,
                        "hedge_action": hedge_action,
                        "hedge_detail": hedge_detail,
                        "iron_fly": "SELL ATM Straddle + BUY Wings",
                        "iron_fly_detail": f"Iron Fly centered at {atm_strike} for premium income",
                        "rationale": rationale,
                        "risk_profile": risk_profile,
                        "confidence": "HIGH" if trade_approved else "MEDIUM",
                        "composition": composition,

                        # 4.1 Leg-wise included in composition

                        # 4.2 Net Credit/Debit
                        "net_premium": {
                            "total_received": round(total_premium_received, 2),
                            "total_paid": round(total_premium_paid, 2),
                            "net_value": round(net_premium, 2),
                            "type": net_premium_type,
                            "futures_entry": round(futures_entry_value, 2)
                        },

                        # 4.3-4.4 Max Profit/Loss
                        "pnl_estimation": {
                            "iron_fly_max_profit": round(iron_fly_max_profit, 2),
                            "futures_max_profit": round(futures_max_profit, 2),
                            "hedge_cost": round(hedge_cost, 2),
                            "max_profit": round(max_profit, 2),
                            "max_profit_zone": max_profit_zone,

                            "futures_loss_till_hedge": round(futures_loss_till_hedge, 2),
                            "hedge_protection": round(hedge_protection_gain, 2),
                            "iron_fly_max_loss": round(iron_fly_max_loss, 2),
                            "max_loss": round(max_loss, 2),
                            "loss_capped": loss_capped,
                            "naked_exposure": naked_exposure,

                            "entry_price": ltp,
                            "current_price": ltp
                        },

                        # 4.5 Break-Even
                        "breakeven": {
                            "upper": round(upper_breakeven, 2),
                            "lower": round(lower_breakeven, 2)
                        },

                        # 4.6 Risk-Reward
                        "risk_reward": {
                            "ratio": round(risk_reward, 2),
                            "classification": trade_classification
                        },

                        # 4.7 Validation
                        "validation": validation,
                        "trade_approved": trade_approved,

                        # Scenario Analysis
                        "scenario_analysis": scenario_analysis,

                        "market_context": {
                            "current_price": ltp,
                            "atm_strike": atm_strike,
                            "instrument": inst_config["display_name"],
                            "hedge_distance": hedge_dist,
                            "wing_distance": wing_dist,
                            "trend_source": "Benchmark Trend Engine (Daily CE/PE vs MA9)",
                            "signal_time": datetime.now().strftime("%H:%M:%S")
                        }
                    }
                else:
                    rec_position = {
                        "direction": "NEUTRAL",
                        "primary_action": "NO ACTION",
                        "primary_action_detail": "Wait for trend confirmation",
                        "rationale": "Neither CE nor PE has confirmed a trend (both above MA9 or conflicting signals)",
                        "pnl_estimation": None,
                        "market_context": {
                            "current_price": ltp,
                            "atm_strike": atm_strike,
                            "instrument": inst_config["display_name"],
                            "trend_source": "Benchmark Trend Engine (Daily CE/PE vs MA9)",
                            "signal_time": "-"
                        }
                    }

                # Replace the legacy benchmark hedge preview with the finalized
                # MCX strategy basket. This is decision support only; execution
                # still requires the existing review/approval flow.
                from src.adaptive_strategy import build_recommendation
                option_quotes = {}
                # When there are not yet ten completed candles, the signal
                # engine intentionally returns WAIT without option symbols.
                # Still show the neutral Strategy 2 plan using the current ATM
                # premiums so the user can review trades to consider.
                ce_for_plan = details.get("ce_symbol") or construct_symbol(symbol, "OPT", expiry, atm_strike, "CE")
                pe_for_plan = details.get("pe_symbol") or construct_symbol(symbol, "OPT", expiry, atm_strike, "PE")
                option_symbols = [ce_for_plan, pe_for_plan]
                try:
                    option_quotes.update(market_data._get_batch_ltp([s for s in option_symbols if s]))
                except Exception:
                    pass
                def plan_quote(trading_symbol):
                    """Return a read-only LTP for every leg used in the preview."""
                    if not trading_symbol:
                        return 0.0
                    if trading_symbol not in option_quotes:
                        try:
                            option_quotes.update(market_data._get_batch_ltp([trading_symbol]))
                        except Exception:
                            pass
                    try:
                        return float(option_quotes.get(trading_symbol) or 0.0)
                    except (TypeError, ValueError):
                        return 0.0
                rec_position = build_recommendation(
                    trend=trend,
                    signal_status=details.get("signal_status", "NO_SIGNAL"),
                    cmp=ltp,
                    atm_strike=atm_strike,
                    expiry=expiry,
                    option_interval=inst_config.get("strike_interval", 5),
                    quote=plan_quote,
                    symbol_builder=construct_symbol,
                    futures_confirmation=details.get("futures_confirmation", "UNKNOWN"),
                    consolidation=(trend == "NEUTRAL"),
                )
                # A futures leg uses the current underlying LTP. Every
                # displayed leg receives an explicit limit price so the UI
                # never presents MKT as an executable price.
                for leg in rec_position.get("legs", []):
                    if leg.get("instrument") == "FUT":
                        leg["premium"] = round(float(ltp or 0.0), 4)
                        leg["data_quality"] = "LIVE_LTP_ESTIMATE"
                    elif leg.get("premium"):
                        leg["data_quality"] = leg.get("data_quality") or "LIVE_LTP_ESTIMATE"
                    else:
                        leg["data_quality"] = "MANUAL_PRICE_REQUIRED"
                rec_position["composition"] = [
                    {"role": f"{leg['action']} {leg['instrument']}", "instrument": leg["instrument"],
                     "action": leg["action"], "strike": leg["strike"] or "MKT", "qty": leg["qty"],
                     "trading_symbol": leg.get("symbol"), "ltp": leg.get("premium") or 0.0,
                     "limit_price": leg.get("premium") or 0.0, "order_type": "LIMIT",
                     "value": (leg.get("premium") or 0.0) * leg["qty"] * lot_size}
                    for leg in rec_position.get("legs", [])
                ]
                rec_position["primary_action"] = rec_position.get("strategy_label", "WAIT / NO TRADE")
                rec_position["primary_action_detail"] = rec_position.get("rationale", "")
                rec_position["hedge_action"] = "PROTECTIVE HEDGES" if rec_position.get("legs") else "WAIT"
                rec_position["hedge_detail"] = rec_position.get("market_context", {}).get("hedge_rule", "")

                event_payload = {
                    "previous_trend": prior_trend,
                    "trend_changed": prior_trend != trend,
                    "recommendation": rec_position,
                    "details": details,
                }
                record_strategy_event(
                    "TREND_ANALYSIS", symbol, trend, details, expiry, atm_strike,
                    ltp, event_payload
                )
                if details.get("futures_confirmation"):
                    record_strategy_event(
                        "FUTURES_CONFIRMATION", symbol, trend, details, expiry,
                        atm_strike, ltp, {"confirmation": details.get("futures_confirmation"),
                                          "futures_close": details.get("futures_close"),
                                          "futures_ma9": details.get("futures_ma9")}
                    )
                if prior_trend != trend:
                    record_strategy_event(
                        "TREND_CHANGE", symbol, trend, details, expiry, atm_strike,
                        ltp, {"previous_trend": prior_trend, "new_trend": trend}
                    )
                    push_snapshot = {
                        "trend": trend,
                        "price": round(float(ltp), 2),
                        "strategy_label": rec_position.get("strategy_label", "WAIT / NO TRADE"),
                        "as_of": datetime.now(TIMEZONE).isoformat(),
                    }
                    threading.Thread(
                        target=__import__("src.mobile_service", fromlist=["send_trend_push"]).send_trend_push,
                        args=(push_snapshot,), daemon=True,
                    ).start()

                # Store in state for persistence
                state_mgr.update("recommended_position", rec_position)
                try:
                    previous_price = float(previous_market_price or 0.0)
                    current_price = float(ltp)
                except (TypeError, ValueError):
                    previous_price, current_price = 0.0, 0.0
                price_change = round(current_price - previous_price, 2) if previous_price else None
                mobile_snapshot = {
                    "symbol": symbol,
                    "trend": trend,
                    "signal_status": details.get("signal_status", "UNKNOWN"),
                    "price": round(current_price, 2),
                    "price_change": price_change,
                    "price_change_percent": round((price_change / previous_price) * 100, 2) if price_change is not None and previous_price else None,
                    "atm_strike": atm_strike,
                    "expiry": expiry,
                    "ce_price": details.get("ce_close", 0),
                    "ce_ma9": details.get("ce_ma9", 0),
                    "pe_price": details.get("pe_close", 0),
                    "pe_ma9": details.get("pe_ma9", 0),
                    "strategy_label": rec_position.get("strategy_label", "WAIT / NO TRADE"),
                    "trade_direction": rec_position.get("direction", "WAIT"),
                    "trade_status": rec_position.get("status", "WAIT"),
                    "trade_approved": bool(rec_position.get("trade_approved", False)),
                    "rationale": rec_position.get("rationale", ""),
                    "legs": [{"action": leg.get("action"), "instrument": leg.get("instrument"),
                              "strike": leg.get("strike"), "option_type": leg.get("option_type"),
                              "quantity": leg.get("qty"), "price": leg.get("premium"),
                              "symbol": leg.get("symbol")}
                             for leg in rec_position.get("legs", [])],
                    "as_of": datetime.now(TIMEZONE).isoformat(),
                }
                state_mgr.update("mobile_market_snapshot", mobile_snapshot)

            except Exception as te:
                print(f"⚠️ Trend calculation failed: {te}")
                trend_data = {"error": str(te)}
                # Fallback: Return neutral position instead of None
                rec_position = {
                    "direction": "NEUTRAL",
                    "primary_action": "NO ACTION",
                    "primary_action_detail": "Trend calculation failed - click Refresh to retry",
                    "rationale": f"Error: {str(te)}",
                    "pnl_estimation": None,
                    "market_context": {
                        "current_price": ltp,
                        "atm_strike": atm_strike,
                        "trend_source": "Error",
                        "signal_time": "-"
                    }
                }

            # Get the trend engine state that was just updated by analyze_trend
            ng_engine_state = state_mgr.get_state().get("ng_trend_engine", {})

            return jsonify({
                "success": True,
                "ltp": ltp,
                "atm_strike": atm_strike,
                "expiry": expiry,
                "trend": trend_data,
                "funds": funds_data,
                "recommended_position": rec_position,
                "ng_trend_engine": ng_engine_state,
                "symbol": symbol,
                "message": "Data refreshed successfully"
            })
        else:
            return jsonify({"success": False, "error": "Failed to fetch market data (LTP is 0 or API error)"})

    except Exception as e:
        print(f"Error in refresh: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/live-orders')
def get_live_orders():
    """
    Fetches live orders from Fyers orderbook.
    Returns today's orders with all details for Order Console.
    """
    try:
        broker = ensure_broker_connected()
        if not broker or not broker.fyers:
            return jsonify({"success": False, "orders": [], "error": "Fyers not connected"})

        orders = broker.get_all_orders()

        return jsonify({
            "success": True,
            "orders": orders,
            "count": len(orders)
        })
    except Exception as e:
        print(f"Error fetching live orders: {e}")
        return jsonify({"success": False, "orders": [], "error": str(e)})


@app.route('/api/live-positions')
def get_live_positions():
    """
    Fetches live positions from Fyers with MTM.
    """
    try:
        from src.rate_limiter import throttle

        broker = ensure_broker_connected()
        if not broker or not broker.fyers:
            return jsonify({"success": False, "positions": [], "error": "Fyers not connected"})

        throttle("get_positions")
        resp = broker.fyers.positions()

        if resp.get("s") == "ok":
            positions = []
            for p in resp.get("netPositions", []):
                positions.append({
                    "symbol": p.get("symbol", "").replace("MCX:", ""),
                    "qty": p.get("netQty", 0),
                    "avg_price": p.get("avgPrice", 0),
                    "ltp": p.get("ltp", 0),
                    "pnl": p.get("pl", 0),
                    "product": p.get("productType", "NRML"),
                    "side": "LONG" if p.get("netQty", 0) > 0 else "SHORT"
                })

            total_pnl = sum([p["pnl"] for p in positions])

            return jsonify({
                "success": True,
                "positions": positions,
                "count": len(positions),
                "total_pnl": total_pnl
            })
        else:
            return jsonify({"success": False, "positions": [], "error": resp.get("message", "")})

    except Exception as e:
        print(f"Error fetching positions: {e}")
        return jsonify({"success": False, "positions": [], "error": str(e)})

# ===== ORDER MANAGEMENT API (NEW) =====

# Shared broker instance for read-only data operations
data_broker = None

def ensure_broker_connected():
    """Connect only with a currently valid manually authorized Fyers token."""
    global data_broker
    from src.broker import FyersConnector
    from src.token_manager import TokenManager

    token_manager = TokenManager.get_instance()
    if not token_manager.is_token_valid():
        data_broker = None
        return None

    current_token = token_manager.access_token
    if data_broker is None or getattr(data_broker, "access_token", None) != current_token:
        data_broker = FyersConnector()
        if not data_broker.connect():
            data_broker = None
    return data_broker

# Alias for backward compatibility
def get_data_broker():
    """Backward compatible alias for ensure_broker_connected()"""
    return ensure_broker_connected()

@app.route('/api/orders/current', methods=['GET'])
# @login_required  <-- DISABLED to allow Dashboard polling without strict session
def get_current_orders():
    """
    Returns the current order execution state for the Orders Tab.
    Includes workflow state for canonical flow tracking.
    Now also updates real-time LTPs for the basket (Cached).
    """
    exec_state = state_mgr.get_state().get("order_execution_state", {}).copy()
    workflow = state_mgr.get_workflow_state()

    # [NEW] Real-time LTP Update Logic (Cached)
    # DISABLED IN ON-DEMAND MODE to save API quota
    from src.config import ON_DEMAND_MODE
    orders = exec_state.get("orders", [])
    status = exec_state.get("status", "IDLE")
    last_updated = exec_state.get("ltp_last_updated", 0)
    now = time.time()

    # Only auto-fetch LTPs in continuous mode (not on-demand)
    # In on-demand mode, user must click "Refresh" button
    if not ON_DEMAND_MODE and orders and (now - last_updated) > 5:
        try:
            broker = get_data_broker()
            # Collect symbols valid for quoting (ignore None/Empty)
            symbols = [o["symbol"] for o in orders if o.get("symbol")]

            if symbols:
                quotes = broker.get_quotes(symbols)
                # Verbose logging to confirm data receipt
                if quotes:
                    print(f"{Fore.MAGENTA}[LTP] Fetched (Fresh): {quotes}{Style.RESET_ALL}")

                    # Update orders locally and in Exec State
                    updated = False
                    for order in orders:
                        sym = order.get("symbol")
                        if sym in quotes:
                            new_ltp = quotes[sym]
                            # Force float conversion for safety
                            try:
                                new_ltp = float(new_ltp)
                            except:
                                continue

                            current_ltp = order.get("ltp")
                            # Update if missing or changed
                            if current_ltp is None or current_ltp != new_ltp:
                                order["ltp"] = new_ltp
                                updated = True

                    if updated:
                        # Save back to Cached State
                        exec_state["orders"] = orders
                        exec_state["ltp_last_updated"] = now
                        state_mgr.update("order_execution_state", exec_state)
                else:
                     print(f"{Fore.RED}[LTP] No quotes received for {symbols}{Style.RESET_ALL}")
        except Exception as e:
            print(f"Error updating LTPs: {e}")

    # Merge workflow state into response
    exec_state["workflow_state"] = workflow["state"]
    exec_state["workflow_description"] = workflow["description"]
    exec_state["workflow_basket_type"] = workflow["basket_type"]
    exec_state["allowed_transitions"] = workflow["allowed_transitions"]

    # [NEW] Add Recommended Position based on Strategy, Trend, and Price
    state = state_mgr.get_state()

    # [FIX] Read trend from ng_trend_engine (same source as Dashboard display)
    # This ensures the Recommended Position matches the "Final Trend" badge
    ng_trend_engine = state.get("ng_trend_engine", {})
    ng_strategy_state = state.get("ng_strategy_state", {})

    # Priority: 1. ng_strategy_state.trend_bias, 2. state.trend, 3. NEUTRAL
    trend = ng_strategy_state.get("trend_bias") or state.get("trend", "NEUTRAL")

    # Also derive trend from ng_trend_engine if available
    if ng_trend_engine:
        ce_leg = ng_trend_engine.get("ce_leg", {})
        pe_leg = ng_trend_engine.get("pe_leg", {})
        ce_below = ce_leg.get("status") == "BELOW"
        pe_below = pe_leg.get("status") == "BELOW"

        # Apply same logic as strategy.py
        if pe_below and not ce_below:
            trend = "BULLISH"
        elif ce_below and not pe_below:
            trend = "BEARISH"
        # If both or neither, keep existing trend or NEUTRAL

    market_price = state.get("market_price", 0)
    atm_strike = state.get("atm_strike", 0)

    # [DEBUG] Log trend derivation for troubleshooting
    print(f"[REC_POS] ng_trend_engine: ce={ng_trend_engine.get('ce_leg', {}).get('status')}, pe={ng_trend_engine.get('pe_leg', {}).get('status')}")
    print(f"[REC_POS] Derived trend: {trend}, market_price: {market_price}, atm_strike: {atm_strike}")

    # Build recommended position object
    # [FIX] Use trend directly (derived from ng_trend_engine) instead of signal_type
    if trend in ["BULLISH", "BEARISH"]:
        if trend == "BULLISH":
            rec_position = {
                "direction": "BULLISH",
                "primary_action": "BUY FUTURES",
                "primary_action_detail": f"Go LONG on NATGASMINI Futures at ₹{market_price:.2f}",
                "hedge_action": "BUY ITM PUT",
                "hedge_detail": f"Buy ITM Put at strike {atm_strike + 5} for downside protection",
                "iron_fly": "SELL ATM Straddle + BUY Wings",
                "iron_fly_detail": f"Iron Fly centered at {atm_strike} for premium income",
                "rationale": "PE < MA9 (Bullish) indicated by Daily Trend Engine. Position aims to profit from upward price movement while hedged against downside.",
                "risk_profile": "HEDGED - Max loss capped by ITM Put + Iron Fly wings",
                "confidence": "HIGH" if trend == "BULLISH" else "MEDIUM"
            }
        else:  # BEARISH
            rec_position = {
                "direction": "BEARISH",
                "primary_action": "SELL FUTURES",
                "primary_action_detail": f"Go SHORT on NATGASMINI Futures at ₹{market_price:.2f}",
                "hedge_action": "BUY ITM CALL",
                "hedge_detail": f"Buy ITM Call at strike {atm_strike - 5} for upside protection",
                "iron_fly": "SELL ATM Straddle + BUY Wings",
                "iron_fly_detail": f"Iron Fly centered at {atm_strike} for premium income",
                "rationale": "PE < MA9 (Bearish) indicated by Daily Trend Engine. Position aims to profit from downward price movement while hedged against upside.",
                "risk_profile": "HEDGED - Max loss capped by ITM Call + Iron Fly wings",
                "confidence": "HIGH" if trend == "BEARISH" else "MEDIUM"
            }

        # Add market context
        rec_position["market_context"] = {
            "current_price": market_price,
            "atm_strike": atm_strike,
            "trend_source": "NG Benchmark Trend Engine (Daily CE/PE vs MA9)",
            "signal_time": exec_state.get("signal_time", "-")
        }

        exec_state["recommended_position"] = rec_position
    else:
        exec_state["recommended_position"] = {
            "direction": "NEUTRAL",
            "primary_action": "NO ACTION",
            "primary_action_detail": "Wait for trend confirmation",
            "rationale": "Neither CE nor PE has confirmed a trend (both above MA9 or conflicting signals)",
            "market_context": {
                "current_price": market_price,
                "atm_strike": atm_strike,
                "trend_source": "NG Benchmark Trend Engine (Daily CE/PE vs MA9)",
                "signal_time": "-"
            }
        }

    return jsonify(exec_state)

@app.route('/api/workflow/state', methods=['GET'])
# @login_required
def get_workflow_state():
    """Returns the current workflow state for the canonical 10-step flow."""
    return jsonify(state_mgr.get_workflow_state())

@app.route('/api/workflow/transition', methods=['POST'])
@login_required
def transition_workflow():
    """
    Transitions the workflow state. Used internally when confirming/executing.
    """
    data = request.get_json() or {}
    new_state = data.get("to_state")
    basket_type = data.get("basket_type")

    if not new_state:
        return jsonify({"status": "error", "message": "Missing to_state"}), 400

    success, message = state_mgr.transition_workflow_state(new_state, basket_type)
    if success:
        return jsonify({"status": "success", "message": message})
    else:
        return jsonify({"status": "error", "message": message}), 400

@app.route('/api/orders/execute', methods=['POST'])
@login_required
def execute_orders():
    """
    Triggers execution of the pending basket.
    Uses OrderExecutor for sequential execution.
    """
    data = request.json
    basket_id = data.get('basket_id')
    qty_multiplier = int(data.get('qty_multiplier', 1)) # [NEW]

    if not basket_id:
        return jsonify({"status": "error", "message": "No basket_id provided"}), 400

    # Check Emergency Halt
    emer = state_mgr.get_state().get("emergency_controls", {})
    if emer.get("is_halted"):
        return jsonify({"status": "error", "message": "TRADING HALTED BY ADMIN"}), 403

    # Execute basket in background thread to avoid blocking
    import threading
    from src.order_executor import OrderExecutor
    from src.broker import FyersConnector

    broker = FyersConnector()
    broker.connect()  # Ensure broker is connected
    executor = OrderExecutor(broker)

    def execute_in_background():
        # Pass multiplier to execution logic
        executor.execute_basket(basket_id, multiplier=qty_multiplier)

    thread = threading.Thread(target=execute_in_background, daemon=True)
    thread.start()

    state_mgr.add_log(f"🚀 Started Basket Execution: {basket_id}")
    return jsonify({"status": "success", "message": f"Basket execution started: {basket_id}"})

@app.route('/api/orders/history', methods=['GET'])
@login_required
def get_order_history():
    """
    Returns historical order executions with full forensics metadata.
    Query params:
        - limit: Max number of baskets (default 50)
        - type: Filter by ENTRY, ADJUSTMENT, or EXIT
    """
    from src.order_executor import OrderExecutor

    limit = request.args.get('limit', 50, type=int)
    basket_type = request.args.get('type', None)

    history = OrderExecutor.get_order_history(limit=limit, basket_type=basket_type)

    # Calculate summary stats
    stats = {
        "total_baskets": len(history),
        "completed": len([b for b in history if b.get("status") == "COMPLETED"]),
        "errors": len([b for b in history if b.get("status") == "ERROR"]),
        "entry_count": len([b for b in history if b.get("basket_type") == "ENTRY"]),
        "adjustment_count": len([b for b in history if b.get("basket_type") == "ADJUSTMENT"]),
        "exit_count": len([b for b in history if b.get("basket_type") == "EXIT"])
    }

    return jsonify({"history": history, "stats": stats})

@app.route('/api/orders/reset', methods=['POST'])
@login_required
def reset_basket():
    """
    Resets the current basket to PENDING state, allowing retry.
    Useful if execution failed due to transient errors (e.g. margin, network).
    """
    exec_state = state_mgr.get_state().get("order_execution_state", {})

    if not exec_state.get("basket_id"):
        return jsonify({"status": "error", "message": "No active basket to reset"}), 400

    # Reset top-level state
    exec_state["status"] = "PENDING"
    exec_state["error_message"] = None
    exec_state["progress"] = 0
    exec_state["current_order_index"] = 0

    # Reset all orders
    for order in exec_state.get("orders", []):
        order["status"] = "PENDING"
        order["error"] = None
        order["order_id"] = None
        order["fill_price"] = None
        order["time"] = None

    state_mgr.update("order_execution_state", exec_state)
    state_mgr.transition_workflow_state("BASKET_READY")

    state_mgr.add_log(f"🔄 Basket Reset: {exec_state['basket_id']}")
    return jsonify({"status": "success", "message": "Basket reset to PENDING"})

# ===== END ORDER MANAGEMENT API =====

# ===== STRATEGY ORCHESTRATION APIs (NEW) =====

@app.route('/api/strategy/state', methods=['GET'])
@login_required
def get_strategy_state():
    """Get current strategy state (IDLE, READY_FOR_ENTRY, IN_POSITION, etc.)"""
    from src.strategy_state import StrategyStateEngine
    engine = StrategyStateEngine.get_instance()
    return jsonify(engine.get_current_state())

@app.route('/api/strategy/state/update', methods=['POST'])
@login_required
@role_required("TRADER")
def update_strategy_state():
    """Manually transition strategy state"""
    from src.strategy_state import StrategyStateEngine, StrategyState

    data = request.json or {}
    new_state_str = data.get("new_state")
    reason = data.get("reason", "Manual transition")

    if not new_state_str:
        return jsonify({"status": "error", "message": "new_state required"}), 400

    try:
        new_state = StrategyState(new_state_str)
    except ValueError:
        return jsonify({"status": "error", "message": f"Invalid state: {new_state_str}"}), 400

    engine = StrategyStateEngine.get_instance()
    result = engine.transition_to(new_state, reason)

    return jsonify(result)

@app.route('/api/orders/master', methods=['GET'])
@login_required
def get_master_orders():
    """Get list of ALL baskets (intents + executions) from DB with advanced filtering"""
    limit = request.args.get('limit', 100, type=int)
    status = request.args.get('status')
    b_type = request.args.get('type')
    strategy = request.args.get('strategy')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    search = request.args.get('search')

    from src.database import UnifiedOrderManager
    db = UnifiedOrderManager.get_instance()
    baskets = db.list_baskets(
        limit=limit,
        status=status,
        basket_type=b_type,
        strategy=strategy,
        start_date=start_date,
        end_date=end_date,
        search=search
    )
    return jsonify({"baskets": baskets})

@app.route('/api/orders/depth/<basket_id>', methods=['GET'])
@login_required
def get_order_depth(basket_id):
    """Get full details of a basket and its legs"""
    from src.database import UnifiedOrderManager
    db = UnifiedOrderManager.get_instance()
    basket = db.get_basket(basket_id)
    if not basket:
        return jsonify({"status": "error", "message": "Basket not found"}), 404
    return jsonify(basket)


@app.route('/api/strategy/events', methods=['GET'])
@login_required
def get_strategy_events():
    """Return the durable trend/confirmation journal for audit and review."""
    from src.database import UnifiedOrderManager
    db = UnifiedOrderManager.get_instance()
    events = db.list_strategy_events(
        limit=request.args.get('limit', 100, type=int),
        symbol=request.args.get('symbol'),
        event_type=request.args.get('event_type'),
        start_date=request.args.get('start_date'),
        end_date=request.args.get('end_date'),
    )
    return jsonify({"events": events, "count": len(events)})


@app.route('/api/strategy/scans', methods=['GET'])
@login_required
def get_strategy_scans():
    from src.database import UnifiedOrderManager
    rows = UnifiedOrderManager.get_instance().list_scan_runs(
        limit=request.args.get('limit', 25, type=int),
        symbol=request.args.get('symbol'),
    )
    return jsonify({"scans": rows, "count": len(rows)})


@app.route('/api/strategy/scan', methods=['POST'])
@login_required
def scan_strategy_history():
    """Scan the last completed daily candles using the live strategy rules.

    This is deliberately manual: one click makes the required Fyers history
    calls, appends the results to the event journal, and stores a scan summary.
    It never places an order and does not alter the existing connection flow.
    """
    import uuid
    from src.database import UnifiedOrderManager
    from src.utils import get_next_expiry_symbol_suffix, get_atm_strike, construct_symbol
    from src.market_data import MarketDataEngine
    from src.strategy import StrategyEngine

    data = request.get_json(silent=True) or {}
    symbol = str(data.get("symbol", "NATGASMINI")).upper()
    try:
        days = max(1, min(int(data.get("days", 20)), 60))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "days must be a number"}), 400

    scan_id = "SCAN_" + uuid.uuid4().hex[:12].upper()
    db = UnifiedOrderManager.get_instance()
    try:
        broker = ensure_broker_connected()
        if not broker or not broker.fyers:
            return jsonify({"status": "error", "message": "Fyers is not connected"}), 503

        expiry = str(data.get("expiry") or "AUTO").upper()
        if expiry in ("", "AUTO"):
            expiry = get_next_expiry_symbol_suffix(symbol=symbol)
        fut_symbol = construct_symbol(symbol, "FUT", expiry)
        market_data = MarketDataEngine(broker, symbol_base=symbol)
        current_fut = market_data._get_ltp(fut_symbol)
        if not current_fut:
            return jsonify({"status": "error", "message": "Unable to determine ATM from futures price"}), 502
        atm = get_atm_strike(current_fut, symbol)
        ce_symbol = construct_symbol(symbol, "OPT", expiry, atm, "CE")
        pe_symbol = construct_symbol(symbol, "OPT", expiry, atm, "PE")
        engine = StrategyEngine(broker, market_data)
        frames = {"fut": engine._fetch_history(fut_symbol),
                  "ce": engine._fetch_history(ce_symbol),
                  "pe": engine._fetch_history(pe_symbol)}

        def by_date(frame):
            if frame is None or frame.empty:
                return {}
            result = {}
            for _, row in frame.iterrows():
                stamp = float(row["epoch"])
                day = datetime.fromtimestamp(stamp, TIMEZONE).strftime("%Y-%m-%d")
                result[day] = {"epoch": int(stamp), "close": float(row["close"])}
            return result

        dated = {name: by_date(frame) for name, frame in frames.items()}
        today_key = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
        common_dates = sorted((set(dated["fut"]) & set(dated["ce"]) & set(dated["pe"])) - {today_key})
        history_quality = {
            "futures_records": len(dated["fut"]), "ce_records": len(dated["ce"]),
            "pe_records": len(dated["pe"]), "common_completed_records": len(common_dates),
            "dropped_for_date_mismatch": max(0, len(set(dated["fut"]) | set(dated["ce"]) | set(dated["pe"])) - len(common_dates)),
            "fallback_used": False, "completed_candles_only": True,
        }
        if len(common_dates) < 10:
            summary = {"scan_id": scan_id, "status": "INSUFFICIENT_HISTORY",
                       "records": len(common_dates), "required": 10, "data_quality": history_quality}
            db.save_scan_run(scan_id, symbol, days, common_dates[0] if common_dates else None,
                             common_dates[-1] if common_dates else None, "INSUFFICIENT_HISTORY", summary)
            return jsonify({"status": "error", "message": "At least 10 common completed daily records are required", "scan": summary}), 422

        # Scan only the requested latest completed records, while retaining
        # enough earlier candles to calculate each MA9 and previous-day cross.
        selected = common_dates[-days:]
        all_dates = common_dates
        first_selected_index = len(all_dates) - len(selected)
        records, previous_trend = [], "NEUTRAL"
        counts = {"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0, "CHANGED": 0}

        for idx, day in enumerate(selected, start=first_selected_index):
            if idx < 9:
                continue
            def close(name, offset=0):
                return dated[name][all_dates[idx + offset]]["close"]
            def ma(name, end_idx):
                values = [dated[name][all_dates[j]]["close"] for j in range(end_idx - 8, end_idx + 1)]
                return sum(values) / 9.0

            ce_prev, ce_now = close("ce", -1), close("ce")
            pe_prev, pe_now = close("pe", -1), close("pe")
            fut_now = close("fut")
            ce_prev_ma, ce_ma = ma("ce", idx - 1), ma("ce", idx)
            pe_prev_ma, pe_ma = ma("pe", idx - 1), ma("pe", idx)
            fut_ma = ma("fut", idx)
            pe_below = pe_now < pe_ma
            ce_below = ce_now < ce_ma
            pe_crossed_below = pe_prev >= pe_prev_ma and pe_now < pe_ma
            pe_crossed_above = pe_prev <= pe_prev_ma and pe_now > pe_ma
            ce_crossed_below = ce_prev >= ce_prev_ma and ce_now < ce_ma
            ce_crossed_above = ce_prev <= ce_prev_ma and ce_now > ce_ma
            both_below = ce_below and pe_below
            futures_confirmation = "BULLISH" if fut_now > fut_ma else ("BEARISH" if fut_now < fut_ma else "CONSOLIDATION")
            raw_trend = "NEUTRAL"
            if pe_crossed_below and ce_crossed_above:
                raw_trend, signal_status = "BULLISH", "PAIRED_BULLISH_CROSS"
            elif ce_crossed_below and pe_crossed_above:
                raw_trend, signal_status = "BEARISH", "PAIRED_BEARISH_CROSS"
            elif both_below:
                raw_trend, signal_status = "NEUTRAL", "BOTH_OPTIONS_BELOW_MA9"
            else:
                raw_trend, signal_status = "NEUTRAL", "NO_DIRECTIONAL_MA9_ALIGNMENT"
            trend = raw_trend
            # Completed option closes versus MA9 are the trend confirmation.
            # Futures remains diagnostic context and never vetoes the setup.
            bullish_alignment = pe_now < pe_ma and ce_now > ce_ma
            bearish_alignment = ce_now < ce_ma and pe_now > pe_ma
            if bullish_alignment:
                trend = "BULLISH"
                if signal_status != "PAIRED_BULLISH_CROSS":
                    signal_status = "TREND_CONTINUES_BULLISH"
            elif bearish_alignment:
                trend = "BEARISH"
                if signal_status != "PAIRED_BEARISH_CROSS":
                    signal_status = "TREND_CONTINUES_BEARISH"
            elif raw_trend == "NEUTRAL" and previous_trend in ("BULLISH", "BEARISH") and both_below:
                trend = previous_trend
                signal_status = f"TREND_CONTINUES_{previous_trend}"

            changed = previous_trend != trend
            if changed: counts["CHANGED"] += 1
            counts[trend] += 1
            detail = {"signal_rule": "STRATEGY_2", "signal_status": signal_status,
                      "raw_trend": raw_trend, "trend_persistence": raw_trend == "NEUTRAL" and trend != "NEUTRAL",
                      "ce_close": round(ce_now, 4), "ce_ma9": round(ce_ma, 4),
                      "pe_close": round(pe_now, 4), "pe_ma9": round(pe_ma, 4),
                      "futures_close": round(fut_now, 4), "futures_ma9": round(fut_ma, 4),
                      "futures_confirmation": futures_confirmation,
                      "bullish_alignment": bullish_alignment, "bearish_alignment": bearish_alignment,
                      "completed_candle_epoch": dated["fut"][day]["epoch"]}
            basket = {"strategy": "STRATEGY_1" if trend in ("BULLISH", "BEARISH") else "STRATEGY_2",
                      "label": "Trending Markets" if trend in ("BULLISH", "BEARISH") else "Consolidation Markets",
                      "action": ("BUY FUTURES + BUY ITM HEDGE + SELL ATM CE/PE" if trend == "BULLISH" else
                                 "SELL FUTURES + BUY ITM HEDGE + SELL ATM CE/PE" if trend == "BEARISH" else
                                 "SELL ATM CE + SELL ATM PE + BUY PROTECTIVE WINGS"),
                      "simulator_available": True}
            payload = {"scan_id": scan_id, "date": day, "previous_trend": previous_trend,
                       "trend_changed": changed, "expiry": expiry, "atm_strike": atm,
                       "symbols": {"fut": fut_symbol, "ce": ce_symbol, "pe": pe_symbol}, "basket": basket}
            candle_at = datetime.fromtimestamp(dated["fut"][day]["epoch"], TIMEZONE).isoformat()
            sequence_no = len(records) + 1
            db.record_strategy_event("HISTORICAL_SIGNAL", symbol=symbol, trend=trend,
                                     signal_status=detail["signal_status"],
                                     futures_confirmation=futures_confirmation,
                                     expiry=expiry, atm_strike=atm, market_price=fut_now,
                                     payload={**payload, "details": detail}, scan_id=scan_id,
                                     sequence_no=sequence_no, candle_at=candle_at,
                                     previous_trend=previous_trend, raw_trend=raw_trend,
                                     dedupe=True)
            if changed:
                db.record_strategy_event("HISTORICAL_TREND_CHANGE", symbol=symbol, trend=trend,
                                         signal_status=detail["signal_status"],
                                         futures_confirmation=futures_confirmation,
                                         expiry=expiry, atm_strike=atm, market_price=fut_now,
                                         payload=payload, scan_id=scan_id,
                                         sequence_no=sequence_no, candle_at=candle_at,
                                         previous_trend=previous_trend, raw_trend=raw_trend,
                                         dedupe=True)
            records.append({"date": day, "trend": trend, "raw_trend": raw_trend, "signal_status": signal_status,
                            "futures_confirmation": futures_confirmation,
                            "ce": {"price": round(ce_now, 4), "ma9": round(ce_ma, 4), "crossed_below": ce_crossed_below, "crossed_above": ce_crossed_above},
                            "pe": {"price": round(pe_now, 4), "ma9": round(pe_ma, 4), "crossed_below": pe_crossed_below, "crossed_above": pe_crossed_above},
                            "futures": {"price": round(fut_now, 4), "ma9": round(fut_ma, 4)}})
            previous_trend = trend

        summary = {"scan_id": scan_id, "status": "COMPLETED", "symbol": symbol,
                   "expiry": expiry, "atm_strike": atm, "records": len(records),
                   "data_quality": history_quality, **counts}
        if records:
            latest = records[-1]
            state_mgr.update("trend", latest["trend"])
            state_mgr.update("ng_strategy_state", {
                **state_mgr.get_state().get("ng_strategy_state", {}),
                "trend_bias": latest["trend"],
                "signal_source": latest["signal_status"],
                "engine_heartbeat": datetime.now().timestamp(),
            })
        db.save_scan_run(scan_id, symbol, days, records[0]["date"] if records else None,
                         records[-1]["date"] if records else None, "COMPLETED", summary)
        return jsonify({"status": "success", "scan": summary, "records": records})
    except Exception as exc:
        summary = {"scan_id": scan_id, "status": "ERROR", "message": str(exc)}
        db.save_scan_run(scan_id, symbol, days, None, None, "ERROR", summary)
        print(f"⚠️ Historical strategy scan failed: {exc}")
        return jsonify({"status": "error", "message": str(exc), "scan": summary}), 500


def _parse_simulation_time(value, field_name):
    if not value:
        raise ValueError(f"{field_name} is required")
    raw = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be ISO date/time") from exc
    # datetime-local values have no offset. They are entered and displayed as IST.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TIMEZONE)
    return parsed, parsed.isoformat()


def _simulation_payoff_profile(legs, underlying_price, lot_size, charges):
    """Build an expiry payoff range from explicitly described option/future legs."""
    try:
        reference = float(underlying_price)
    except (TypeError, ValueError):
        return [], None, None, []
    if reference <= 0:
        return [], None, None, []

    from src.instrument_config import get_instrument_config
    interval = float(get_instrument_config("NATGASMINI").get("strike_interval", 5))
    points = sorted({max(interval, round((reference * (1 + shift)) / interval) * interval)
                     for shift in (-.30, -.20, -.15, -.10, -.05, 0, .05, .10, .15, .20, .30)})
    profile = []
    for price in points:
        pnl = 0.0
        for leg in legs:
            side = 1 if leg["side"] == "BUY" else -1
            qty = float(leg["qty"])
            instrument = str(leg.get("instrument") or "").upper()
            option_type = str(leg.get("option_type") or "").upper()
            strike = leg.get("strike")
            entry = float(leg["entry_price"])
            if instrument == "FUT":
                pnl += (price - entry) * qty * lot_size * side
            elif option_type in ("CE", "PE") and strike is not None:
                intrinsic = max(price - float(strike), 0) if option_type == "CE" else max(float(strike) - price, 0)
                pnl += (intrinsic - entry) * qty * lot_size * side
        profile.append({"underlying_price": round(price, 2), "pnl": round(pnl - charges, 2)})

    break_evens = []
    for left, right in zip(profile, profile[1:]):
        if left["pnl"] == 0:
            break_evens.append(left["underlying_price"])
        elif (left["pnl"] < 0 < right["pnl"]) or (right["pnl"] < 0 < left["pnl"]):
            span = right["pnl"] - left["pnl"]
            if span:
                value = left["underlying_price"] + (0 - left["pnl"]) * (right["underlying_price"] - left["underlying_price"]) / span
                break_evens.append(round(value, 2))
    return profile, min(point["pnl"] for point in profile), max(point["pnl"] for point in profile), sorted(set(break_evens))


def _simulation_risk_warnings(legs):
    warnings = []
    shorts = [leg for leg in legs if leg["side"] == "SELL" and str(leg.get("option_type") or "").upper() in ("CE", "PE")]
    for short in shorts:
        option_type = str(short.get("option_type")).upper()
        strike = short.get("strike")
        protected = any(
            leg["side"] == "BUY" and str(leg.get("option_type") or "").upper() == option_type
            and leg.get("strike") is not None and strike is not None
            and ((option_type == "CE" and float(leg["strike"]) > float(strike))
                 or (option_type == "PE" and float(leg["strike"]) < float(strike)))
            for leg in legs
        )
        if not protected:
            warnings.append(f"{option_type} short at {strike or 'unknown strike'} has no detected protective wing.")
    return warnings


@app.route('/api/simulations', methods=['GET'])
@login_required
def list_simulations():
    from src.database import UnifiedOrderManager
    rows = UnifiedOrderManager.get_instance().list_simulations(
        limit=request.args.get('limit', 50, type=int),
        basket_id=request.args.get('basket_id'),
    )
    return jsonify({"simulations": rows, "count": len(rows)})


@app.route('/api/simulations/<simulation_id>', methods=['DELETE'])
@login_required
def delete_simulation(simulation_id):
    from src.database import UnifiedOrderManager
    deleted = UnifiedOrderManager.get_instance().delete_simulation(simulation_id)
    if not deleted:
        return jsonify({"status": "error", "message": "Simulation not found"}), 404
    return jsonify({"status": "success", "simulation_id": simulation_id})


@app.route('/api/simulations/plan/<int:event_id>', methods=['GET'])
@login_required
def get_signal_simulation_plan(event_id):
    """Build the exact strategy template for a saved signal without trading."""
    from src.database import UnifiedOrderManager
    from src.utils import construct_symbol
    from src.adaptive_strategy import build_recommendation
    from src.instrument_config import get_instrument_config

    event = UnifiedOrderManager.get_instance().get_strategy_event(event_id)
    if not event:
        return jsonify({"status": "error", "message": "Journal event not found"}), 404
    if event.get("event_type") not in ("HISTORICAL_SIGNAL", "HISTORICAL_TREND_CHANGE"):
        return jsonify({"status": "error", "message": "Only saved strategy signals can be simulated"}), 400

    payload = event.get("payload") or {}
    details = payload.get("details") or {}
    symbol = str(event.get("symbol") or "NATGASMINI").upper()
    expiry = str(event.get("expiry") or payload.get("expiry") or "").upper()
    atm = event.get("atm_strike") or payload.get("atm_strike")
    cmp = event.get("market_price") or details.get("futures_close")
    try:
        atm, cmp = int(float(atm)), float(cmp)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "This signal is missing its ATM strike or futures price"}), 400

    prices = {
        (payload.get("symbols") or {}).get("fut"): details.get("futures_close", cmp),
        (payload.get("symbols") or {}).get("ce"): details.get("ce_close", 0),
        (payload.get("symbols") or {}).get("pe"): details.get("pe_close", 0),
    }
    def quote(trading_symbol):
        try:
            return float(prices.get(trading_symbol) or 0)
        except (TypeError, ValueError):
            return 0.0

    def make_symbol(base, kind, item_expiry, strike, option_type):
        return construct_symbol(base, kind, item_expiry, strike, option_type)

    recommendation = build_recommendation(
        trend=event.get("trend") or "NEUTRAL",
        signal_status=event.get("signal_status") or details.get("signal_status") or "HISTORICAL_SIGNAL",
        cmp=cmp, atm_strike=atm, expiry=expiry,
        option_interval=get_instrument_config(symbol).get("strike_interval", 5),
        quote=quote, symbol_builder=make_symbol,
        futures_confirmation=event.get("futures_confirmation") or details.get("futures_confirmation") or "UNKNOWN",
        consolidation=(event.get("trend") == "NEUTRAL"),
    )
    # Historical scans persist ATM CE/PE/FUT closes. Hedge-leg history was not
    # part of the original scan payload, so use a read-only current quote as a
    # clearly labelled fallback when a valid existing FYERS token is available.
    try:
        broker = ensure_broker_connected()
        missing_symbols = [leg.get("symbol") for leg in recommendation.get("legs", [])
                           if not leg.get("premium") and leg.get("symbol")]
        live_quotes = broker.get_quotes(missing_symbols) if broker and missing_symbols else {}
    except Exception:
        live_quotes = {}
    for leg in recommendation.get("legs", []):
        if leg.get("instrument") == "FUT":
            leg["premium"] = cmp
        if not leg.get("premium"):
            live_price = live_quotes.get(leg.get("symbol"))
            if live_price:
                leg["premium"] = round(float(live_price), 4)
                leg["data_quality"] = "LIVE_LTP_ESTIMATE"
            else:
                leg["data_quality"] = "MANUAL_PRICE_REQUIRED"
        else:
            leg["data_quality"] = "HISTORICAL_CLOSE"
    return jsonify({
        "status": "success", "source_event": {
            "id": event["id"], "candle_at": event.get("candle_at"), "trend": event.get("trend"),
            "signal_status": event.get("signal_status"), "expiry": expiry, "atm_strike": atm,
        },
        "symbol": symbol, "lot_size": get_instrument_config(symbol).get("lot_size", 1),
        "recommendation": recommendation,
    })


@app.route('/api/simulations', methods=['POST'])
@login_required
def run_simulation():
    """Run and save a price/quantity/time what-if for a displayed basket.

    Prices are supplied per leg because options and futures have different
    premiums. The calculation is deliberately transparent: signed price
    change × quantity × lot size, plus holding duration and calendar fields.
    It does not place orders or call FYERS.
    """
    import uuid
    from src.database import UnifiedOrderManager
    from src.instrument_config import get_instrument_config

    data = request.get_json(silent=True) or {}
    db = UnifiedOrderManager.get_instance()
    basket_id = data.get("basket_id")
    basket = db.get_basket(basket_id) if basket_id else None
    if basket_id and not basket:
        return jsonify({"status": "error", "message": "Basket not found"}), 404

    raw_legs = data.get("legs")
    if raw_legs is None and basket:
        raw_legs = basket.get("orders", [])
    if not isinstance(raw_legs, list) or not raw_legs:
        return jsonify({"status": "error", "message": "A basket with at least one leg is required"}), 400

    try:
        entry_dt, entry_at = _parse_simulation_time(data.get("entry_at"), "entry_at")
        exit_dt, exit_at = _parse_simulation_time(data.get("exit_at"), "exit_at")
        if exit_dt < entry_dt:
            raise ValueError("exit_at must be after entry_at")
        multiplier = float(data.get("quantity_multiplier", 1))
        if multiplier <= 0:
            raise ValueError("quantity_multiplier must be greater than zero")
        symbol_base = str(data.get("symbol", "NATGASMINI")).upper()
        lot_size = float(data.get("lot_size") or get_instrument_config(symbol_base).get("lot_size", 1))
        if lot_size <= 0:
            raise ValueError("lot_size must be greater than zero")
    except (TypeError, ValueError) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    entry_prices = data.get("entry_prices") or {}
    exit_prices = data.get("exit_prices") or {}
    quantity_overrides = data.get("quantities") or {}
    charges_input = data.get("charges") or {}
    market_model = data.get("market_model") or {}
    execution_model = data.get("execution_model") or {}
    try:
        brokerage_per_quantity = max(0.0, float(charges_input.get("brokerage_per_quantity", 0)))
        tax_percent = max(0.0, float(charges_input.get("tax_percent", 0)))
        exchange_percent = max(0.0, float(charges_input.get("exchange_percent", 0)))
        ctt_percent = max(0.0, float(charges_input.get("ctt_percent", 0)))
        sebi_percent = max(0.0, float(charges_input.get("sebi_percent", 0)))
        stamp_percent = max(0.0, float(charges_input.get("stamp_percent", 0)))
        gst_percent = max(0.0, float(charges_input.get("gst_percent", 0)))
        slippage_percent = max(0.0, float(charges_input.get("slippage_percent", 0)))
        margin_rate = max(0.0, float(charges_input.get("margin_rate", 12)))
        hedge_benefit_percent = min(100.0, max(0.0, float(charges_input.get("hedge_benefit_percent", 0))))
        capital_available = float(data.get("capital_available") or 0)
        implied_volatility = max(0.0, float(market_model.get("implied_volatility", 0)))
        volatility_change = float(market_model.get("volatility_change", 0))
        fill_probability = min(100.0, max(0.0, float(execution_model.get("fill_probability", 100))))
        partial_fill_percent = min(100.0, max(0.0, float(execution_model.get("partial_fill_percent", 100))))
        order_delay_minutes = max(0.0, float(execution_model.get("order_delay_minutes", 0)))
        stop_loss = max(0.0, float(data.get("stop_loss_amount") or 0))
        target_profit = max(0.0, float(data.get("target_profit_amount") or 0))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Simulator assumptions must be numeric"}), 400
    simulated_legs, errors = [], []
    total_pnl = total_entry_value = total_exit_value = total_turnover = total_brokerage = 0.0

    for index, leg in enumerate(raw_legs, 1):
        key = str(leg.get("seq", index))
        symbol = leg.get("symbol") or leg.get("trading_symbol") or f"LEG_{key}"
        entry_raw = entry_prices.get(key, entry_prices.get(symbol, leg.get("limit_price") or leg.get("entry_limit_price") or leg.get("entry") or leg.get("fill_price") or leg.get("ltp")))
        exit_raw = exit_prices.get(key, exit_prices.get(symbol, leg.get("exit") or leg.get("mark_price") or leg.get("ltp")))
        try:
            entry_price = float(entry_raw)
            exit_price = float(exit_raw)
            qty = float(quantity_overrides.get(key, quantity_overrides.get(symbol, leg.get("qty", 0)))) * multiplier
            qty *= partial_fill_percent / 100.0
            if entry_price < 0 or exit_price < 0 or qty <= 0:
                raise ValueError("price must be non-negative and quantity positive")
        except (TypeError, ValueError):
            errors.append(f"Leg {key} ({symbol}) needs numeric entry and exit prices and quantity")
            continue

        side = str(leg.get("side") or leg.get("action") or "BUY").upper()
        if side not in ("BUY", "SELL"):
            errors.append(f"Leg {key} ({symbol}) must have BUY or SELL side")
            continue
        direction = 1 if side == "BUY" else -1
        # Slippage always worsens the assumed fill: buys pay more on entry and
        # receive less on exit; sells receive less on entry and pay more on exit.
        slip = slippage_percent / 100
        adjusted_entry = entry_price * (1 + slip if side == "BUY" else 1 - slip)
        adjusted_exit = exit_price * (1 - slip if side == "BUY" else 1 + slip)
        entry_value = adjusted_entry * qty * lot_size
        exit_value = adjusted_exit * qty * lot_size
        pnl = (adjusted_exit - adjusted_entry) * qty * lot_size * direction
        brokerage = brokerage_per_quantity * qty
        total_entry_value += entry_value if side == "BUY" else -entry_value
        total_exit_value += exit_value if side == "BUY" else -exit_value
        total_pnl += pnl
        total_turnover += abs(entry_value) + abs(exit_value)
        total_brokerage += brokerage
        simulated_legs.append({
            "seq": leg.get("seq", index), "symbol": symbol, "side": side,
            "instrument": str(leg.get("instrument") or leg.get("leg_type") or "OPT").upper(),
            "option_type": str(leg.get("option_type") or "").upper() or None,
            "strike": leg.get("strike"), "qty": qty,
            "entry_price": entry_price, "exit_price": exit_price,
            "adjusted_entry_price": round(adjusted_entry, 4), "adjusted_exit_price": round(adjusted_exit, 4),
            "entry_value": round(entry_value, 2), "exit_value": round(exit_value, 2),
            "gross_pnl": round(pnl, 2), "brokerage": round(brokerage, 2),
            "bid": leg.get("bid"), "ask": leg.get("ask"),
            "limit_price": leg.get("limit_price") or leg.get("entry_limit_price"),
            "implied_volatility": leg.get("implied_volatility", implied_volatility),
            "order_type": str(leg.get("order_type") or execution_model.get("order_type") or "LIMIT").upper(),
            "pnl": round(pnl - brokerage, 2), "holding_hours": round((exit_dt - entry_dt).total_seconds() / 3600, 4),
        })

    if errors:
        return jsonify({"status": "error", "message": "Missing simulator inputs", "errors": errors}), 400

    generic_tax = total_turnover * (tax_percent / 100)
    exchange_charges = total_turnover * (exchange_percent / 100)
    ctt_charges = total_turnover * (ctt_percent / 100)
    sebi_charges = total_turnover * (sebi_percent / 100)
    stamp_charges = total_turnover * (stamp_percent / 100)
    gst_charges = (total_brokerage + exchange_charges + sebi_charges) * (gst_percent / 100)
    taxes = generic_tax + ctt_charges + stamp_charges + gst_charges
    total_charges = total_brokerage + taxes + exchange_charges + sebi_charges
    net_pnl = total_pnl - total_charges
    margin_notional = sum(abs(leg["entry_value"]) for leg in simulated_legs)
    estimated_margin = margin_notional * (margin_rate / 100) * (1 - hedge_benefit_percent / 100)
    risk_warnings = _simulation_risk_warnings(simulated_legs)
    if fill_probability < 100:
        risk_warnings.append(f"Limit fill probability assumption is {fill_probability:.1f}%.")
    if partial_fill_percent < 100:
        risk_warnings.append(f"Partial fills reduce simulated quantity to {partial_fill_percent:.1f}%.")
    if order_delay_minutes:
        risk_warnings.append(f"Execution delay assumption: {order_delay_minutes:g} minutes.")
    option_legs = [leg for leg in simulated_legs if leg.get("instrument") == "OPT"]
    if option_legs and implied_volatility <= 0:
        risk_warnings.append("No implied volatility supplied; option time value remains an estimate.")
    if market_model.get("quote_timestamp"):
        market_model["quote_timestamp"] = str(market_model["quote_timestamp"])
    if stop_loss and net_pnl <= -abs(stop_loss):
        risk_warnings.append("Simulated P&L reaches the configured stop-loss.")
    if target_profit and net_pnl >= abs(target_profit):
        risk_warnings.append("Simulated P&L reaches the configured target.")
    payoff_profile, max_loss, max_profit, break_evens = _simulation_payoff_profile(
        simulated_legs, data.get("underlying_price") or data.get("atm_strike") or data.get("market_price"),
        lot_size, total_brokerage + taxes,
    )
    result = {
        "status": "COMPLETED", "basket_id": basket_id,
        "entry_at": entry_at, "exit_at": exit_at,
        "entry_day": entry_dt.strftime("%A"), "exit_day": exit_dt.strftime("%A"),
        "entry_hour": entry_dt.strftime("%H:%M"), "exit_hour": exit_dt.strftime("%H:%M"),
        "holding_hours": round((exit_dt - entry_dt).total_seconds() / 3600, 4),
        "lot_size": lot_size, "quantity_multiplier": multiplier,
        "strategy": data.get("strategy"), "source_event_id": data.get("source_event_id"),
        "data_quality": data.get("data_quality", "MANUAL_ASSUMPTION"),
        "exit_reason": data.get("exit_reason", "CUSTOM_EXIT"),
        "legs": simulated_legs, "gross_pnl": round(total_pnl, 2),
        "brokerage": round(total_brokerage, 2), "taxes": round(taxes, 2),
        "charges_breakdown": {"brokerage": round(total_brokerage, 2), "generic_tax": round(generic_tax, 2), "exchange": round(exchange_charges, 2), "ctt": round(ctt_charges, 2), "sebi": round(sebi_charges, 2), "stamp": round(stamp_charges, 2), "gst": round(gst_charges, 2)},
        "total_charges": round(total_charges, 2), "total_pnl": round(net_pnl, 2),
        "net_entry_value": round(total_entry_value, 2),
        "net_exit_value": round(total_exit_value, 2),
        "estimated_margin": round(estimated_margin, 2), "capital_available": capital_available or None,
        "assumptions": {"market_model": market_model, "execution_model": execution_model, "charges": charges_input, "stop_loss_amount": stop_loss or None, "target_profit_amount": target_profit or None},
        "capital_shortfall": round(max(0, estimated_margin - capital_available), 2) if capital_available else None,
        "return_on_entry_pct": round((net_pnl / abs(total_entry_value) * 100), 4) if total_entry_value else None,
        "return_on_capital_pct": round((net_pnl / estimated_margin * 100), 4) if estimated_margin else None,
        "payoff_profile": payoff_profile, "max_loss_estimate": max_loss,
        "max_profit_estimate": max_profit, "break_evens": break_evens,
        "risk_warnings": risk_warnings,
    }
    simulation_id = "SIM_" + uuid.uuid4().hex[:12].upper()
    db.save_simulation(simulation_id, basket_id, entry_at, exit_at, multiplier, data, result)
    result["simulation_id"] = simulation_id
    record_strategy_event("SIMULATION_RUN", symbol_base, (basket or {}).get("direction"), {},
                          (basket or {}).get("expiry"), (basket or {}).get("atm_strike"),
                          None, {"simulation_id": simulation_id, "basket_id": basket_id,
                                 "source_event_id": data.get("source_event_id"),
                                 "total_pnl": result["total_pnl"], "data_quality": result["data_quality"]})
    return jsonify({"status": "success", "simulation": result})

@app.route('/api/orders/action', methods=['POST'])
@login_required
@role_required("TRADER")
def handle_order_action():
    """Unified action handler (Approve, Cancel, Execute, Retry)"""
    data = request.json or {}
    basket_id = data.get("basket_id")
    action = data.get("action")

    if not basket_id or not action:
        return jsonify({"status": "error", "message": "basket_id and action required"}), 400

    from src.order_intent import OrderIntentEngine
    from src.order_executor import OrderExecutor
    from src.database import UnifiedOrderManager

    db = UnifiedOrderManager.get_instance()
    basket = db.get_basket(basket_id)

    if not basket:
        return jsonify({"status": "error", "message": "Basket not found"}), 404

    broker = get_data_broker()
    intent_engine = OrderIntentEngine.get_instance(broker)
    intent_engine._current_intent = basket # Point engine to this basket

    if action == "APPROVE":
        result = intent_engine.validate_intent()
        return jsonify(result)

    elif action == "CANCEL":
        reason = data.get("reason", "User cancelled via console")
        result = intent_engine.cancel_intent(reason)
        return jsonify(result)

    elif action == "EXECUTE":
        # Check Strategy State
        from src.strategy_state import StrategyStateEngine, StrategyState
        strat_engine = StrategyStateEngine.get_instance()

        # Transition strat state to EXECUTING if it was READY_FOR_ENTRY
        if strat_engine.current_state == StrategyState.READY_FOR_ENTRY:
             strat_engine.transition_to(StrategyState.IN_POSITION, "Execution started by user")

        # Mark intent as executing
        intent_engine.mark_executing()

        # Start executor
        executor = OrderExecutor(broker)
        def run_exec():
             executor.execute_basket(basket_id)

        import threading
        thread = threading.Thread(target=run_exec, daemon=True)
        thread.start()

        return jsonify({"success": True, "message": f"Execution started for {basket_id}"})

    elif action == "RETRY":
        # Reset and Execute
        basket["status"] = "PENDING"
        for o in basket.get("orders", []):
            o["status"] = "PENDING"
            o["order_id"] = None
        db.upsert_basket(basket)
        return jsonify({"success": True, "message": "Basket reset. Please click Execute."})

    return jsonify({"status": "error", "message": f"Unknown action: {action}"}), 400

@app.route('/api/adjustment/check', methods=['POST'])
@login_required
def check_adjustments():
    """Check if any adjustment rules are triggered"""
    from src.adjustment_engine import AdjustmentEngine

    broker = get_data_broker()
    if not broker:
        return jsonify({"error": "Broker not available"}), 503

    symbol_base = request.json.get('symbol', 'NATGASMINI') if request.json else 'NATGASMINI'
    engine = AdjustmentEngine(broker, symbol_base)
    result = engine.check_adjustments()

    return jsonify(result)

@app.route('/api/adjustment/trigger', methods=['POST'])
@login_required
@role_required("TRADER")
def trigger_adjustment():
    """Manually trigger an adjustment"""
    from src.adjustment_engine import AdjustmentEngine

    data = request.json or {}
    action = data.get("action")
    reason = data.get("reason", "Manual trigger")

    if not action:
        return jsonify({"status": "error", "message": "action required"}), 400

    broker = get_data_broker()
    engine = AdjustmentEngine(broker)
    result = engine.trigger_adjustment(action, reason)

    return jsonify(result)

@app.route('/api/adjustment/status', methods=['GET'])
@login_required
def get_adjustment_status():
    """Get current adjustment engine status"""
    from src.adjustment_engine import AdjustmentEngine
    broker = get_data_broker()
    engine = AdjustmentEngine(broker)
    return jsonify(engine.get_adjustment_status())

# ===== END STRATEGY ORCHESTRATION APIs =====

@app.route('/api/toggle_status', methods=['POST'])
@login_required
def toggle_status():
    state = state_mgr.get_state()
    current = state.get("status", "IDLE")
    new_val = "STOPPED" if current != "STOPPED" else "IDLE"
    state_mgr.update("status", new_val)
    state_mgr.add_log(f"⚡ SYSTEM STATUS: {new_val}")
    return jsonify({"status": "success", "system_status": new_val})

@app.route('/api/reject_trade', methods=['POST'])
@login_required
def reject_trade():
    """
    User manually rejects/dismisses the pending trade.
    """
    data = request.json
    symbol = data.get('symbol', 'NATGASMINI')

    state_mgr.update("pending_signal", None, symbol=symbol)
    state_mgr.add_log(f"🚫 USER COMMAND [{symbol}]: Trade Rejected/Dismissed.")
    return jsonify({"status": "success", "message": f"Signal Cleared for {symbol}"})

@app.route('/api/clear_cache', methods=['POST'])
@login_required
def clear_cache():
    state_mgr.clear_logs()
    return jsonify({"status": "success", "message": "Logs Cleared"})

@app.route('/api/set_multiplier', methods=['POST'])
@login_required
def set_multiplier():
    data = request.json
    val = int(data.get('multiplier', 1))
    if val < 1: val = 1
    state_mgr.update("setup_multiplier", val)
    state_mgr.add_log(f"🔢 SETUP QUANTITY: Updated to {val}x", category="SETUP", user=session.get('username'))
    return jsonify({"status": "success", "multiplier": val})

# --- USER MANAGEMENT API ---

@app.route('/api/user/change_password', methods=['POST'])
@login_required
def change_password():
    data = request.json
    current_password = data.get("current_password")
    new_password = data.get("new_password")

    username = session.get('username')
    state_mgr = StateManager.get_instance()

    # Verify current password first
    token, user = state_mgr.user_manager.authenticate(username, current_password)
    if not user:
         return jsonify({"status": "error", "message": "Incorrect current password"}), 401

    success, msg = state_mgr.user_manager.change_password(username, new_password)

    if success:
        state_mgr.add_log(f"Password changed for {username}", category="AUTH", user=username)
        return jsonify({"status": "success", "message": "Password updated successfully"})
    else:
        return jsonify({"status": "error", "message": msg}), 400

@app.route('/api/admin/export_logs', methods=['GET'])
@login_required
@role_required("ADMIN")
def export_logs_csv():
    import csv
    import io
    from flask import Response

    state_mgr = StateManager.get_instance()
    logs = getattr(state_mgr, 'logs', []) # Access raw structured logs

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Headers
    writer.writerow(['Timestamp', 'Category', 'User', 'Message'])

    # Data
    for log in logs:
        # Handle both dict (structured) and str (legacy) logs if any mixed
        if isinstance(log, dict):
             writer.writerow([log.get('timestamp',''), log.get('category',''), log.get('user',''), log.get('message','')])
        else:
             writer.writerow(['-', 'LEGACY', '-', str(log)])

    output.seek(0)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename=audit_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"}
    )

# --- ADMIN API ENDPOINTS ---
@app.route('/api/admin/update_config', methods=['POST'])
@login_required
@role_required("ADMIN") # Enforce Admin Role
def admin_update_config():
    data = request.json
    state_mgr = StateManager.get_instance()

    current_conf = state_mgr.get_state().get("admin_config", {})

    updated_keys = []
    for k, v in data.items():
        if k in current_conf: # Only update existing keys
            current_conf[k] = v
            updated_keys.append(k)

    if updated_keys:
        state_mgr.update("admin_config", current_conf)
        state_mgr.add_log(f"🛠️ ADMIN CONFIG: Updated {', '.join(updated_keys)}", category="ADMIN", user=session.get('username'))
        state_mgr.add_alert("INFO", "ADMIN", f"Configuration updated by {session.get('username')}: {', '.join(updated_keys)}")

    return jsonify({"status": "success", "config": current_conf})

@app.route('/api/admin/emergency', methods=['POST'])
@login_required
@role_required("RISK_MANAGER") # Risk Manager or above
def admin_emergency():
    data = request.json
    action = data.get("action")
    reason = data.get("reason", "No reason provided")
    user = session.get('username')

    state_mgr = StateManager.get_instance()
    emer_state = state_mgr.get_state().get("emergency_controls", {}).copy()

    log_msg = f"EMERGENCY ACTION: {action} by {user}. Reason: {reason}"
    state_mgr.add_log(log_msg, category="CRITICAL", user=user)

    if action == "HALT":
        emer_state["is_halted"] = True
        state_mgr.add_alert("CRITICAL", "ADMIN", f"🚨 TRADING HALTED by {user}: {reason}")
    elif action == "RESUME":
        emer_state["is_halted"] = False
        state_mgr.add_alert("WARNING", "ADMIN", f"Trading Resumed by {user}")
    elif action == "FORCE_FLAT":
        emer_state["force_flat"] = True
        state_mgr.add_alert("CRITICAL", "ADMIN", f"🚨 FORCE FLAT TRIGGERED by {user}: {reason}")
        # In a real system, this would trigger an immediate specialized thread
        # For now, the main bot loop should pick this up
    elif action == "FORCE_OBS":
        emer_state["force_observation"] = True
        state_mgr.add_alert("WARNING", "ADMIN", f"Forced Observation Mode by {user}: {reason}")

    state_mgr.update("emergency_controls", emer_state)
    return jsonify({"status": "success", "action": action, "state": emer_state})

@app.route('/api/admin/auth_url', methods=['GET'])
@login_required
@role_required("ADMIN")
def get_auth_url():
    from src.token_manager import TokenManager
    tm = TokenManager.get_instance()
    url = tm.get_login_url()
    if url:
        return jsonify({"status": "success", "url": url})
    return jsonify({"status": "error", "message": "App ID/Secret missing or config error"}), 400

@app.route('/callback', methods=['GET'])
def fyers_callback():
    auth_code = request.args.get('auth_code')
    if not auth_code:
        return "Error: No Auth Code received", 400

    from src.token_manager import TokenManager
    tm = TokenManager.get_instance()
    res = tm.generate_token_from_code(auth_code)

    if res["success"]:
        state_mgr = StateManager.get_instance()
        state_mgr.add_log("✅ LOGIN SUCCESSFUL via Admin Panel", category="ADMIN", user="system")
        return """
        <div style="font-family: sans-serif; text-align: center; padding: 50px; background: #0f172a; color: white;">
            <h1 style="color: #10b981;">✅ Login Successful!</h1>
            <p>The access token has been generated and saved.</p>
            <p>You can close this window now.</p>
            <script>setTimeout(function(){ window.close(); }, 3000);</script>
        </div>
        """
    else:
        return f"""
        <div style="font-family: sans-serif; text-align: center; padding: 50px; background: #0f172a; color: white;">
            <h1 style="color: #ef4444;">❌ Login Failed</h1>
            <p>{res['message']}</p>
        </div>
        """, 400

@app.route('/api/admin/email-notifications', methods=['GET', 'POST'])
@app.route('/api/admin/mailjet', methods=['GET', 'POST'])
@login_required
@role_required("ADMIN")
def admin_email_notifications():
    from src.notification_service import load_provider_settings, save_provider_settings, provider_status
    provider_settings = load_provider_settings()
    state_cfg = state_mgr.get_state().get("email_notification_config") or {}
    saved_cfg = provider_settings.get("notifications", {}) or {}
    cfg = {**saved_cfg, **state_cfg}
    if request.method == 'GET':
        recipients = cfg.get("recipients") or cfg.get("recipient", "") or provider_settings.get("recipients", [])
        if isinstance(recipients, str):
            from src.notification_service import normalize_recipients
            recipients = normalize_recipients(recipients)
        return jsonify({"enabled": cfg.get("enabled", False),
                        "notify_trends": cfg.get("notify_trends", True),
                        "notify_bullish": cfg.get("notify_bullish", True),
                        "notify_bearish": cfg.get("notify_bearish", True),
                        "notify_neutral": cfg.get("notify_neutral", False),
                        "notify_potential_trends": cfg.get("notify_potential_trends", True),
                        "notify_confirmed_trends": cfg.get("notify_confirmed_trends", True),
                        "notify_recovery": cfg.get("notify_recovery", True),
                        "notify_weakening": cfg.get("notify_weakening", True),
                        "notify_data_quality": cfg.get("notify_data_quality", True),
                        "notify_risk": cfg.get("notify_risk", True),
                        "notify_expiry": cfg.get("notify_expiry", True),
                        "delivery_mode": cfg.get("delivery_mode", "immediate"),
                        "delay_hours": cfg.get("delay_hours", 0),
                        "delay_minutes": cfg.get("delay_minutes", 0),
                        "recipient": ", ".join(recipients),
                        "recipients": recipients,
                        "provider": "brevo",
                        "brevo_transport": provider_settings.get("brevo_transport", "api"),
                        "brevo_smtp_host": provider_settings.get("brevo_smtp_host", "smtp-relay.brevo.com"),
                        "brevo_smtp_port": provider_settings.get("brevo_smtp_port", 587),
                        "brevo_smtp_login": provider_settings.get("brevo_smtp_login", ""),
                        "from_email": provider_settings.get("from_email", ""),
                        "from_name": provider_settings.get("from_name", "NG Automation Bot"),
                        **provider_status(provider_settings)})
    data = request.get_json(silent=True) or {}
    from src.notification_service import normalize_recipients, DEFAULT_FROM_EMAIL
    raw_recipients = data.get("recipients", data.get("recipient", ""))
    recipient_parts = raw_recipients if isinstance(raw_recipients, list) else str(raw_recipients or "").replace(";", ",").replace("\n", ",").split(",")
    recipient_parts = [str(email).strip() for email in recipient_parts if str(email).strip()]
    recipients = normalize_recipients(recipient_parts)
    if len(recipients) != len(recipient_parts):
        return jsonify({"status": "error", "message": "Enter valid recipient email addresses."}), 400
    recipient = ", ".join(recipients)
    from_email = str(data.get("from_email", provider_settings.get("from_email") or DEFAULT_FROM_EMAIL)).strip()
    if from_email and ("@" not in from_email or len(from_email) > 254):
        return jsonify({"status": "error", "message": "Enter a valid verified sender email."}), 400
    provider = "brevo"
    try:
        delay_hours = int(data.get("delay_hours", 0))
        delay_minutes = int(data.get("delay_minutes", 0))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Delay must use whole hours and minutes."}), 400
    if not (0 <= delay_hours <= 24 and 0 <= delay_minutes <= 59):
        return jsonify({"status": "error", "message": "Hours must be 0 to 24 and minutes 0 to 59."}), 400
    delivery_mode = str(data.get("delivery_mode", "immediate"))
    if delivery_mode not in ("immediate", "custom_delay"):
        return jsonify({"status": "error", "message": "Choose immediate or custom delay delivery."}), 400
    if delivery_mode == "custom_delay" and delay_hours == 0 and delay_minutes == 0:
        return jsonify({"status": "error", "message": "Custom delay must be at least one minute."}), 400
    try:
        smtp_port = int(data.get("brevo_smtp_port", provider_settings.get("brevo_smtp_port", 587)))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Brevo SMTP port must be a number."}), 400
    if not (1 <= smtp_port <= 65535):
        return jsonify({"status": "error", "message": "Brevo SMTP port must be between 1 and 65535."}), 400
    smtp_host = str(data.get("brevo_smtp_host", provider_settings.get("brevo_smtp_host", "smtp-relay.brevo.com"))).strip() or "smtp-relay.brevo.com"
    smtp_login = str(data.get("brevo_smtp_login", provider_settings.get("brevo_smtp_login", ""))).strip()
    transport = str(data.get("brevo_transport", provider_settings.get("brevo_transport", "api"))).lower()
    if transport not in ("api", "smtp"):
        return jsonify({"status": "error", "message": "Choose Brevo API or Brevo SMTP."}), 400
    provider_settings.update({"provider": provider, "from_email": from_email,
                              "recipients": recipients,
                              "brevo_transport": transport, "brevo_smtp_host": smtp_host,
                              "brevo_smtp_port": smtp_port, "brevo_smtp_login": smtp_login,
                              "from_name": str(data.get("from_name", provider_settings.get("from_name", "NG Automation Bot"))).strip() or "NG Automation Bot"})
    secret_fields = ("brevo_api_key", "brevo_smtp_key", "mailjet_api_key", "mailjet_secret_key")
    for field in secret_fields:
        value = str(data.get(field, "")).strip()
        if value:
            provider_settings[field] = value
    cfg.update({"enabled": bool(data.get("enabled")),
                "notify_trends": bool(data.get("notify_trends", True)),
                "notify_bullish": bool(data.get("notify_bullish", True)),
                "notify_bearish": bool(data.get("notify_bearish", True)),
                "notify_neutral": bool(data.get("notify_neutral")),
                "notify_potential_trends": bool(data.get("notify_potential_trends", True)),
                "notify_confirmed_trends": bool(data.get("notify_confirmed_trends", True)),
                "notify_recovery": bool(data.get("notify_recovery", True)),
                "notify_weakening": bool(data.get("notify_weakening", True)),
                "notify_data_quality": bool(data.get("notify_data_quality", True)),
                "notify_risk": bool(data.get("notify_risk", True)),
                "notify_expiry": bool(data.get("notify_expiry", True)),
                "delivery_mode": delivery_mode,
                "delay_hours": delay_hours,
                "delay_minutes": delay_minutes,
                "recipient": recipient,
                "recipients": recipients})
    provider_settings["notifications"] = cfg
    try:
        save_provider_settings(provider_settings)
    except OSError:
        return jsonify({"status": "error", "message": "Could not save email provider settings on this server."}), 500
    state_mgr.update("email_notification_config", cfg)
    state_mgr.add_log(f"{provider.title()} email notification settings updated", category="ADMIN", user=session.get('username'))
    return jsonify({"status": "success", "message": "Email notification settings saved."})

@app.route('/api/admin/email-notifications/logs', methods=['GET'])
@app.route('/api/admin/mailjet/logs', methods=['GET'])
@login_required
@role_required("ADMIN")
def admin_email_notification_logs():
    from src.database import UnifiedOrderManager
    limit = request.args.get("limit", 100, type=int)
    return jsonify({"logs": UnifiedOrderManager.get_instance().list_email_notification_logs(limit)})

@app.route('/api/admin/email-notifications/test', methods=['POST'])
@app.route('/api/admin/mailjet/test', methods=['POST'])
@login_required
@role_required("ADMIN")
def admin_email_notifications_test():
    from src.notification_service import send_email_notification, load_provider_settings
    provider_settings = load_provider_settings()
    state_cfg = state_mgr.get_state().get("email_notification_config") or {}
    saved_cfg = provider_settings.get("notifications", {}) or {}
    cfg = {**saved_cfg, **state_cfg}
    provider = "brevo"
    recipients = cfg.get("recipients") or cfg.get("recipient") or provider_settings.get("recipients")
    provider_settings["recipients"] = recipients
    data = request.get_json(silent=True) or {}
    sample_type = str(data.get("alert_type") or "TEST").upper()
    samples = {
        "TEST": ("NG Bot Brevo test", "Email notifications are configured and reachable."),
        "CONFIRMED_TREND_CHANGE": ("NG sample: confirmed trend change", "SAMPLE ONLY — Confirmed trend changed from NEUTRAL to BULLISH after a completed daily candle.\nReview the active basket."),
        "PROVISIONAL_REVERSAL": ("NG sample: possible reversal", "SAMPLE ONLY — Live prices suggest a possible reversal. This is not confirmed until the daily candle closes."),
        "PROVISIONAL_NEUTRAL": ("NG sample: possible neutral shift", "SAMPLE ONLY — Both live options are below MA9. This is not confirmed until the daily candle closes."),
        "WEAKENING": ("NG sample: position weakening", "SAMPLE ONLY — One option has lost the confirmed trend alignment intraday. Monitor risk; no basket switch is confirmed."),
        "RECOVERY": ("NG sample: trend alignment recovered", "SAMPLE ONLY — Live option prices have realigned with the confirmed trend. No basket switch is required."),
        "DATA_QUALITY": ("NG sample: data-quality warning", "SAMPLE ONLY — CE/PE quote data is missing or stale. Trend and basket decisions should be paused."),
        "RISK": ("NG sample: risk alert", "SAMPLE ONLY — Risk or exit condition detected. Review margin, loss limits, and the active basket."),
        "EXPIRY": ("NG sample: expiry alert", "SAMPLE ONLY — Contract expiry or rollover requires review before the next basket is used."),
    }
    if sample_type not in samples:
        return jsonify({"status": "error", "message": "Unknown notification sample."}), 400
    subject, message = samples[sample_type]
    if send_email_notification(subject, message, recipients,
                                settings=provider_settings, alert_type=sample_type):
        return jsonify({"status": "success", "message": "Test email accepted by the provider."})
    reason = provider_settings.get("_last_error") or "Check provider credentials, verified sender, and recipient."
    return jsonify({"status": "error", "message": f"Test email failed: {reason}"}), 400

# FYERS credential login routes retained from the existing application behavior.
@app.route('/api/admin/save_credentials', methods=['POST'])
@login_required
@role_required("ADMIN")
def save_broker_credentials():
    data = request.get_json(silent=True) or {}
    app_id, secret_id = data.get("app_id"), data.get("secret_id")
    fyers_id, pin, totp_key = data.get("fyers_id"), data.get("pin"), data.get("totp_key")
    if not all([app_id, secret_id, fyers_id, pin, totp_key]):
        return jsonify({"status": "error", "message": "All fields are required"}), 400
    from src.token_manager import TokenManager
    TokenManager.save_headless_credentials(fyers_id, pin, totp_key, app_id, secret_id)
    state_mgr.add_log("Broker credentials updated", category="ADMIN", user=session.get('username'))
    return jsonify({"status": "success", "message": "Credentials saved securely"})

@app.route('/api/admin/test_login', methods=['POST'])
@login_required
@role_required("ADMIN")
def test_headless_login():
    from src.token_manager import TokenManager
    result = TokenManager.perform_headless_login()
    if result["success"]:
        return jsonify({"status": "success", "message": "Login successful. Token generated."})
    return jsonify({"status": "error", "message": f"Login failed: {result['message']}"}), 400

@app.route('/api/admin/credential_status', methods=['GET'])
@login_required
@role_required("ADMIN")
def get_credential_status():
    from dotenv import load_dotenv
    load_dotenv(override=True)
    creds = {key: bool(os.getenv(env)) for key, env in {
        "fyers_id": "FYERS_ID", "pin": "FYERS_PIN", "totp_key": "FYERS_TOTP_KEY",
        "app_id": "FYERS_APP_ID", "secret_id": "FYERS_SECRET_ID"
    }.items()}
    complete = all(creds.values())
    return jsonify({"status": "success", "all_configured": complete,
                    "is_logged_in": bool(os.getenv("FYERS_ACCESS_TOKEN")),
                    "credentials": creds,
                    "message": "All credentials configured" if complete else "Some credentials missing"})

@app.route('/api/admin/logout_fyers', methods=['POST'])
@login_required
@role_required("ADMIN")
def admin_logout_fyers():
    try:
        from dotenv import unset_key
        env_path = os.path.join(os.getcwd(), '.env')
        unset_key(env_path, "FYERS_ACCESS_TOKEN")
        unset_key(env_path, "FYERS_REFRESH_TOKEN")
        os.environ.pop("FYERS_ACCESS_TOKEN", None)
        os.environ.pop("FYERS_REFRESH_TOKEN", None)
        state_mgr.add_log("Fyers tokens cleared by manual logout", category="ADMIN", user=session.get('username'))
        return jsonify({"status": "success", "message": "Logged out from Fyers. Tokens cleared."})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500

@app.route('/api/audit/logs', methods=['GET'])
@login_required
@role_required("VIEWER") # All authenticated users can view logs
def get_audit_logs():
    # Return structured logs for CSV export
    state_mgr = StateManager.get_instance()
    return jsonify({"logs": state_mgr.logs})

def run_web_server():
    from src.config import WEB_PORT
    print(f"🚀 Web Interface Launching on http://0.0.0.0:{WEB_PORT}")
    # Setting debug=False because reloader in main thread causes issues
    app.run(host="0.0.0.0", port=WEB_PORT, debug=False, use_reloader=False)

if __name__ == "__main__":
    run_web_server()
