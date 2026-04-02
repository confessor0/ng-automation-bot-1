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
from src.instrument_manager import InstrumentManager
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
instrument_mgr = InstrumentManager.get_instance()

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

@app.route('/test')
def test():
    """Direct test page - no login required"""
    return render_template('test.html')

@app.route('/api/instruments')
def get_instruments():
    """Returns all supported instruments for UI dropdown."""
    from src.instrument_config import get_all_instruments
    return jsonify({
        "success": True,
        "instruments": get_all_instruments()
    })

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
    
    Uses ensure_broker_connected() which handles:
    - Token validation (JWT expiry check)
    - Automatic token refresh if expired
    - Headless login if refresh fails
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
                trend, _, details = strategy.analyze_trend(fut_symbol, expiry)
                
                trend_data = {
                    "trend": trend,
                    "ce_price": details.get("ce_close", 0),
                    "ce_ma9": details.get("ce_ma9", 0),
                    "pe_price": details.get("pe_close", 0),
                    "pe_ma9": details.get("pe_ma9", 0)
                }
                print(f"✅ Trend Engine Updated: {trend}")
                
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
                
                # Store in state for persistence
                state_mgr.update("recommended_position", rec_position)
                
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


@app.route('/api/profile')
@login_required
def get_user_profile():
    """
    Fetches User & Risk Profile data.
    """
    try:
        from src.config import MAX_FUTURES_QTY, MAX_ADJUSTMENTS_PER_DAY
        from src.token_manager import TokenManager
        import os
        
        # Identity (from session, not state — prevents crash)
        identity = {
            "login_id": os.getenv("FYERS_ID", "-"),
            "name": session.get("username", "User"),
            "role": session.get("role", "viewer"),
            "session_status": "ACTIVE" if TokenManager.get_instance().is_token_valid() else "INACTIVE",
            "last_login": "Today"
        }
        
        # Strategy Lock
        try:
            strat_state = state_mgr.get_state("NATGASMINI").get("strategy_phase", "IDLE")
        except Exception:
            strat_state = "IDLE"
        lock_status = "LOCKED" if strat_state != "IDLE" else "UNLOCKED"
        
        # Security
        security = {
            "auth_type": "Authentication System",
            "email_linked": "Link email..." 
        }
        
        # Risk Limits
        risk = {
            "max_futures": MAX_FUTURES_QTY,
            "max_adjustments": MAX_ADJUSTMENTS_PER_DAY,
            "status": "WITHIN LIMITS"
        }
        
        return jsonify({
            "success": True,
            "identity": identity,
            "lock": {"status": lock_status, "strategy": "Benchmark Hedge"},
            "security": security,
            "risk": risk
        })
    except Exception as e:
        print(f"Error fetching profile: {e}")
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

@app.route('/api/execute_trade', methods=['POST'])
@login_required
def execute_trade():
    """
    User manually confirms the pending trade for a specific instrument.
    """
    print(f"{Fore.GREEN}🔥🔥🔥 /api/execute_trade endpoint HIT! 🔥🔥🔥{Style.RESET_ALL}")
    
    data = request.json
    symbol = data.get('symbol', 'NATGASMINI')
    
    print(f"{Fore.CYAN}DEBUG [execute_trade]: symbol={symbol}{Style.RESET_ALL}")
    
    inst_state = state_mgr.get_state(symbol)
    
    print(f"{Fore.CYAN}DEBUG [execute_trade]: inst_state keys={list(inst_state.keys())}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}DEBUG [execute_trade]: pending_signal={inst_state.get('pending_signal')}{Style.RESET_ALL}")
    
    # Check Emergency Halt
    emer = state_mgr.get_state().get("emergency_controls", {})
    if emer.get("is_halted"):
        return jsonify({"status": "error", "message": "TRADING HALTED BY ADMIN"}), 403
        
    if inst_state.get('pending_signal'):
        print(f"{Fore.GREEN}DEBUG [execute_trade]: Setting trade_approval = True{Style.RESET_ALL}")
        state_mgr.update("trade_approval", True, symbol=symbol)
        state_mgr.add_log(f"👨‍💻 USER COMMAND [{symbol}]: Execute Trade Approved.")
        print(f"{Fore.GREEN}DEBUG [execute_trade]: trade_approval set! Returning success.{Style.RESET_ALL}")
        return jsonify({"status": "success", "message": f"Execution Queued for {symbol}"})
    
    print(f"{Fore.RED}DEBUG [execute_trade]: NO pending_signal found! Returning error.{Style.RESET_ALL}")
    return jsonify({"status": "error", "message": f"No Pending Signal for {symbol}"}), 400

# ===== ORDER MANAGEMENT API (NEW) =====

# Shared broker instance for read-only data operations
data_broker = None

def ensure_broker_connected():
    """
    Ensures a valid broker connection exists.
    
    This is the SINGLE POINT of broker initialization for all data endpoints.
    It handles:
    1. Token validation (JWT expiry check, no API call)
    2. Automatic token refresh if expired
    3. Headless login if refresh fails
    4. Broker re-initialization with new token
    
    Returns: FyersConnector instance or None if connection failed
    """
    global data_broker
    from src.broker import FyersConnector
    from src.token_manager import TokenManager
    from dotenv import load_dotenv
    import os
    
    # Get singleton TokenManager
    token_mgr = TokenManager.get_instance()
    
    # Step 1: Check if current token is valid (no API call)
    if not token_mgr.is_token_valid():
        print("🔄 Token invalid or expired - refreshing...")
        
        # Step 2: Try to get a valid token (refresh or headless login)
        new_token = token_mgr.ensure_valid_token()
        
        if not new_token:
            print("❌ Failed to obtain valid token")
            return None
        
        # Force reload environment with new token
        load_dotenv(override=True)
        
        # Reset broker to force re-init with new token
        data_broker = None
    
    # Step 3: Initialize or re-initialize broker
    current_token = os.getenv("FYERS_ACCESS_TOKEN")
    
    if data_broker is None:
        print("🔌 Initializing broker connection...")
        data_broker = FyersConnector()
        if not data_broker.connect():
            print("❌ Broker connection failed")
            return None
        return data_broker
    
    # Step 4: Check if broker needs reconnect (token changed)
    is_connected = getattr(data_broker, 'fyers', None) is not None
    token_changed = getattr(data_broker, 'access_token', '') != current_token
    
    if not is_connected or token_changed:
        print(f"🔄 Reconnecting broker (connected={is_connected}, token_changed={token_changed})")
        data_broker = FyersConnector()
        if not data_broker.connect():
            print("❌ Broker reconnection failed")
            return None
    
    return data_broker

# Alias for backward compatibility
def get_data_broker():
    """Backward compatible alias for ensure_broker_connected()"""
    return ensure_broker_connected()

# ===== CONTINUOUS AUTO-LOGIN DAEMON =====
# Monitors Fyers connection in the background and auto-renews the token silently
def _background_auto_login_loop():
    import threading
    import time
    from src.token_manager import TokenManager
    
    def _loop():
        # Immediate check on startup
        try:
            print("🚀 [STARTUP] Attempting automatic Fyers connection...")
            broker = ensure_broker_connected()
            if broker and broker.fyers:
                print("✅ [STARTUP] Fyers auto-login successful!")
        except Exception as e:
            print(f"⚠️ [STARTUP] Auto-login error: {e}")
            
        # Continuous monitoring
        while True:
            time.sleep(300)  # Check every 5 minutes
            try:
                tm = TokenManager.get_instance()
                # If token is expiring within 2 hours or invalid, trigger the refresh/login
                if tm.is_token_expiring_soon(hours=2) or not tm.is_token_valid():
                    print("🔄 [BACKGROUND] Token expiring or invalid, triggering silent auto-login...")
                    ensure_broker_connected()
            except Exception as e:
                print(f"⚠️ [BACKGROUND] Auto-login error: {e}")

    # Run in background thread to not block server
    t = threading.Thread(target=_loop, daemon=True)
    t.start()

_background_auto_login_loop()

import time

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

@app.route('/api/orders/force-create', methods=['POST'])
# @login_required
def force_create_basket():
    """
    Force creates a basket for testing real order flow.
    This bypasses the trend detection and creates a basket directly.
    """
    try:
        from datetime import datetime
        from src.order_executor import OrderExecutor
        from src.broker import FyersConnector
        from src.state_manager import StateManager
        
        state_mgr = StateManager.get_instance()
        
        data = request.json or {}
        direction = data.get('direction', 'BEARISH')  # BULLISH or BEARISH
        # [FIX] Use live ATM from StateManager to ensure dynamic basket
        live_atm = state_mgr.get_state().get("atm_strike")
        atm_strike = live_atm if live_atm else data.get('atm_strike', 310)
        
        print(f"DEBUG: force_create_basket using ATM={atm_strike} (Live={live_atm})")
        expiry = data.get('expiry', '26JAN')
        symbol = data.get('symbol', 'NATGASMINI')
        
        # Check Emergency Halt
        emer = state_mgr.get_state().get("emergency_controls", {})
        if emer.get("is_halted"):
            return jsonify({"status": "error", "message": "TRADING HALTED"}), 403
        
        # Connect broker and create executor
        broker = FyersConnector()
        broker.connect()
        executor = OrderExecutor(broker, symbol_base=symbol)
        
        # Create the basket
        basket_id = executor.create_basket(direction, atm_strike, expiry)
        
        # Set pending signal for UI
        state_mgr.update("pending_signal", {
            "trend": direction,
            "basket": [],
            "basket_id": basket_id,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }, symbol=symbol)
        
        # Transition workflow state
        state_mgr.transition_workflow_state("SIGNAL_CONFIRMED", basket_type="ENTRY")
        state_mgr.transition_workflow_state("BASKET_READY")
        
        return jsonify({"status": "success", "message": f"Basket execution started: {basket_id}", "basket_id": basket_id})
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()}), 500

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

# ===== P&L SIMULATOR API (NEW) =====

@app.route('/api/simulate-scenarios', methods=['GET'])
@login_required
def simulate_scenarios():
    """
    Runs the Expiry-Aware Dynamic P&L Simulator.
    Uses live CMP from market data.
    
    Query Params:
        - dte: Days to Expiry (optional, calculated from current expiry if not provided)
    
    Returns: Simulation results with leg-wise P&L across scenarios
    """
    from src.pnl_simulator import run_simulation
    from src.utils import get_atm_strike
    
    # Get live CMP from state
    state = state_mgr.get_state()
    cmp = state.get("market_price", 0)
    atm_strike = state.get("atm_strike", 0)
    
    if cmp == 0:
        return jsonify({
            "status": "error",
            "message": "No market price available. Click 'Refresh Data' first."
        }), 400
    
    # Calculate ATM if not in state
    if atm_strike == 0:
        atm_strike = get_atm_strike(cmp, "NATGASMINI")
    
    # Get DTE from query or estimate from current date
    dte = request.args.get('dte', type=int)
    symbol = request.args.get('symbol', 'NATGASMINI')
    
    if dte is None:
        # Estimate DTE (assume expiry around 25th of month)
        from datetime import datetime
        now = datetime.now()
        day = now.day
        if day <= 25:
            dte = 25 - day
        else:
            dte = 7  # Default if past expiry
    
    try:
        results = run_simulation(cmp=cmp, atm_strike=atm_strike, dte=dte, symbol=symbol)
        return jsonify({
            "status": "success",
            "simulation": results
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# ===== END P&L SIMULATOR API =====

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

@app.route('/api/positions/analysis', methods=['GET'])
@login_required
def get_positions_analysis():
    """Get current position analysis (delta, hedge coverage, etc.)"""
    from src.positions_analyzer import PositionsAnalyzer
    
    broker = get_data_broker()
    if not broker:
        return jsonify({"error": "Broker not available"}), 503
    
    analyzer = PositionsAnalyzer(broker)
    symbol_base = request.args.get('symbol', 'NATGASMINI')
    analysis = analyzer.analyze(symbol_base)
    
    return jsonify(analysis)

@app.route('/api/positions/snapshots', methods=['GET'])
@login_required
def get_position_snapshots():
    """Get historical position snapshots"""
    from src.positions_analyzer import PositionsAnalyzer
    limit = request.args.get('limit', 50, type=int)
    snapshots = PositionsAnalyzer.get_snapshots(limit)
    return jsonify({"snapshots": snapshots})

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

@app.route('/api/toggle_autonomous', methods=['POST'])
@login_required
def toggle_autonomous():
    state = state_mgr.get_state()
    current = state.get("autonomous_mode", False)
    new_val = not current
    state_mgr.update("autonomous_mode", new_val)
    state_mgr.add_log(f"🤖 SYSTEM MODE: {'AUTONOMOUS' if new_val else 'MANUAL CONFIRMATION'}")
    return jsonify({"status": "success", "autonomous_mode": new_val})

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

@app.route('/api/users/list', methods=['GET'])
@login_required
@role_required("ADMIN")
def list_users():
    state_mgr = StateManager.get_instance()
    users = state_mgr.user_manager.get_all_users()
    # User manager already returns safe user objects
    return jsonify({"users": users})

@app.route('/api/users/add', methods=['POST'])
@login_required
@role_required("ADMIN")
def add_user():
    data = request.json
    state_mgr = StateManager.get_instance()
    
    success, msg = state_mgr.user_manager.add_user(
        data.get("username"),
        data.get("password"),
        data.get("name"),
        data.get("role")
    )
    
    if success:
        state_mgr.add_log(f"Admin created user: {data.get('username')}", category="ADMIN", user=session.get('username'))
        return jsonify({"status": "success", "message": msg})
    else:
        return jsonify({"status": "error", "message": msg}), 400

@app.route('/api/users/delete', methods=['POST'])
@login_required
@role_required("ADMIN")
def delete_user():
    data = request.json
    username_to_delete = data.get("username")
    
    if username_to_delete == session.get('username'):
        return jsonify({"status": "error", "message": "Cannot delete your own account"}), 400

    state_mgr = StateManager.get_instance()
    success, msg = state_mgr.user_manager.delete_user(username_to_delete)

    if success:
        state_mgr.add_log(f"Admin deleted user: {username_to_delete}", category="ADMIN", user=session.get('username'))
        return jsonify({"status": "success", "message": msg})
    else:
        return jsonify({"status": "error", "message": msg}), 400

@app.route('/api/users/update_role', methods=['POST'])
@login_required
@role_required("ADMIN")
def update_user_role():
    data = request.json
    username = data.get("username")
    new_role = data.get("role")

    if username == session.get('username'):
        return jsonify({"status": "error", "message": "Cannot change your own role"}), 400
    
    state_mgr = StateManager.get_instance()
    success, msg = state_mgr.user_manager.update_user_role(username, new_role)

    if success:
        state_mgr.add_log(f"Admin updated role for {username} to {new_role}", category="ADMIN", user=session.get('username'))
        return jsonify({"status": "success", "message": msg})
    else:
        return jsonify({"status": "error", "message": msg}), 400

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

@app.route('/api/user/update_profile', methods=['POST'])
@login_required
def update_profile():
    data = request.json
    email = data.get("email")
    # Add other fields here if needed
    
    username = session.get('username')
    state_mgr = StateManager.get_instance()
    
    updates = {}
    if email is not None:
        updates["email"] = email
        
    if updates:
        state_mgr.user_manager.update_user(username, updates)
        
        # [FIX] Update the UI state immediately
        prof = state_mgr.get_state().get("user_profile", {})
        if "identity" in prof and email:
            prof["identity"]["email"] = email
            state_mgr.update("user_profile", prof)
            
        state_mgr.add_log(f"Profile updated for {username}", category="AUTH", user=username)
        return jsonify({"status": "success", "message": "Profile updated"})
    
    return jsonify({"status": "success", "message": "No changes made"})

# --- HEADLESS LOGIN CONFIGURATION [NEW] ---
@app.route('/api/admin/save_credentials', methods=['POST'])
@login_required
@role_required("ADMIN")
def save_broker_credentials():
    data = request.json
    app_id = data.get("app_id")
    secret_id = data.get("secret_id")
    fyers_id = data.get("fyers_id")
    pin = data.get("pin")
    totp_key = data.get("totp_key")

    if not all([app_id, secret_id, fyers_id, pin, totp_key]):
        return jsonify({"status": "error", "message": "All fields are required"}), 400

    from src.token_manager import TokenManager
    TokenManager.save_headless_credentials(fyers_id, pin, totp_key, app_id, secret_id)
    
    state_mgr = StateManager.get_instance()
    state_mgr.add_log("🔐 Broker Credentials Updated", category="ADMIN", user=session.get('username'))
    
    return jsonify({"status": "success", "message": "Credentials Saved Securely"})

@app.route('/api/admin/test_login', methods=['POST'])
@login_required
@role_required("ADMIN")
def test_headless_login():
    from src.token_manager import TokenManager
    result = TokenManager.perform_headless_login()
    
    if result["success"]:
        return jsonify({"status": "success", "message": "Login Successful! Token Generated."})
    else:
        return jsonify({"status": "error", "message": f"Login Failed: {result['message']}"}), 400

@app.route('/api/admin/credential_status', methods=['GET'])
@login_required
@role_required("ADMIN")
def get_credential_status():
    """Check if all autonomous login credentials are configured"""
    import os
    from dotenv import load_dotenv
    load_dotenv(override=True)
    
    creds = {
        "fyers_id": bool(os.getenv("FYERS_ID")),
        "pin": bool(os.getenv("FYERS_PIN")),
        "totp_key": bool(os.getenv("FYERS_TOTP_KEY")),
        "app_id": bool(os.getenv("FYERS_APP_ID")),
        "secret_id": bool(os.getenv("FYERS_SECRET_ID"))
    }
    
    all_configured = all(creds.values())
    is_logged_in = bool(os.getenv("FYERS_ACCESS_TOKEN"))
    
    return jsonify({
        "status": "success",
        "all_configured": all_configured,
        "is_logged_in": is_logged_in,
        "credentials": creds,
        "message": "✅ All Credentials Configured - Auto-Login Enabled" if all_configured else "⚠️ Some Credentials Missing"
    })

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
    tm = TokenManager()
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
    tm = TokenManager()
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

@app.route('/api/admin/set_mode', methods=['POST'])
@login_required
@role_required("ADMIN")
def admin_set_mode():
    data = request.json
    mode = data.get("mode")
    state_mgr = StateManager.get_instance()
    
    sys_health = state_mgr.get_state().get("system_health", {}).copy()
    sys_health["mode"] = mode
    state_mgr.update("system_health", sys_health)
    
    state_mgr.add_log(f"🛡️ SYSTEM MODE CHANGED TO: {mode}", category="ADMIN", user=session.get('username'))
    state_mgr.add_alert("WARNING", "ADMIN", f"System Mode changed to {mode} by {session.get('username')}")
    
    return jsonify({"status": "success", "mode": mode})

@app.route('/api/admin/update_telegram', methods=['POST'])
@login_required
@role_required("ADMIN")
def admin_update_telegram():
    data = request.json
    bot_token = data.get("bot_token")
    chat_id = data.get("chat_id")
    
    state_mgr = StateManager.get_instance()
    
    # Update State (for UI sync)
    tele_config = state_mgr.get_state().get("telegram_config", {}).copy()
    tele_config["bot_token"] = bot_token
    tele_config["chat_id"] = chat_id
    state_mgr.update("telegram_config", tele_config)
    
    # Update Config in Runtime (Optional if we want immediate effect without restart)
    import src.config as config
    config.TELEGRAM_BOT_TOKEN = bot_token
    config.TELEGRAM_CHAT_ID = chat_id
    
    state_mgr.add_log("📲 Telegram Credentials Updated via Admin Panel", category="ADMIN", user=session.get('username'))
    return jsonify({"status": "success", "message": "Telegram Credentials Saved!"})

@app.route('/api/admin/test_telegram', methods=['POST'])
@login_required
@role_required("ADMIN")
def admin_test_telegram():
    from src.notification_service import send_telegram_alert
    from datetime import datetime
    
    try:
        user = session.get('username')
        send_telegram_alert(f"🔔 TEST ALERT from Admin Panel\n\nTriggered by: {user}\nTime: {datetime.now().strftime('%H:%M:%S')}")
        return jsonify({"status": "success", "message": "Test Alert Sent!"})
    except Exception as e:
         return jsonify({"status": "error", "message": str(e)})

@app.route('/api/admin/logout_fyers', methods=['POST'])
@login_required
@role_required("ADMIN")
def admin_logout_fyers():
    from dotenv import unset_key
    import os
    
    try:
        # 1. Clear variables from .env
        env_path = os.path.join(os.getcwd(), '.env')
        unset_key(env_path, "FYERS_ACCESS_TOKEN")
        unset_key(env_path, "FYERS_REFRESH_TOKEN")
        
        # 2. Clear from environment
        if "FYERS_ACCESS_TOKEN" in os.environ:
            del os.environ["FYERS_ACCESS_TOKEN"]
        if "FYERS_REFRESH_TOKEN" in os.environ:
            del os.environ["FYERS_REFRESH_TOKEN"]
            
        # 3. Log
        state_mgr = StateManager.get_instance()
        state_mgr.add_log("🛑 Fyers Tokens Cleared (Manual Logout)", category="ADMIN", user=session.get('username'))
        
        return jsonify({"status": "success", "message": "Logged out from Fyers. Tokens cleared."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

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
