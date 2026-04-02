from datetime import datetime, timedelta
import pandas as pd
from colorama import Fore, Style
from src.utils import get_atm_strike, construct_symbol
from src.state_manager import StateManager

class StrategyEngine:
    # [FIX] Class-level cache to persist across instances (requests)
    _history_cache = {}
    _history_ttl = 300 # 5 minutes

    def __init__(self, broker, market_data):
        self.broker = broker
        self.state_manager = StateManager.get_instance()
        self.market_data = market_data  # Reference to market data interface
        self.ma_period = 9  # [NEW] Default 9 MA
        
    def analyze_trend(self, fut_symbol, expiry):
        """
        Analyzes trend based on ATM Options vs MA9.
        ENHANCED: Uses real-time LTP as primary source, history as backup.
        """
        print(f"📊 Analyzing Trend for {fut_symbol} (Exp: {expiry})")
        
        # 1. Get Latest Future Price to determine ATM
        fut_ltp = self.market_data._get_ltp(fut_symbol)
        if fut_ltp == 0:
            # Try direct broker quote
            quotes = self.broker.get_quotes([fut_symbol])
            fut_ltp = quotes.get(fut_symbol, 0)
            
        if fut_ltp == 0:
            print(f"{Fore.RED}❌ Cannot get Future LTP. Trend = NEUTRAL{Style.RESET_ALL}")
            self.state_manager.update("trend", "NEUTRAL")
            return "NEUTRAL", 0, {}
            
        # 2. Derive ATM Strike
        base_symbol = self.market_data.symbol_base
        atm_strike = get_atm_strike(fut_ltp, base_symbol)
        
        # 3. Construct Options Symbols
        ce_symbol = construct_symbol(base_symbol, "OPT", expiry, atm_strike, "CE")
        pe_symbol = construct_symbol(base_symbol, "OPT", expiry, atm_strike, "PE")
        
        # 4. Get Real-Time LTPs for Options (PRIMARY SOURCE)
        # [OPTIMIZED] Use MarketDataEngine's cached batch fetch to reuse data if available (e.g. from parent call)
        quotes = self.market_data._get_batch_ltp([ce_symbol, pe_symbol])
        ce_close = quotes.get(ce_symbol, 0)
        pe_close = quotes.get(pe_symbol, 0)
        
        print(f"   Real-time Quotes: CE={ce_close}, PE={pe_close}")
        
        # 5. Fetch History for MA9 Calculation
        ce_hist = self._fetch_history(ce_symbol)
        pe_hist = self._fetch_history(pe_symbol)
        
        # Calculate MA9 (if history available)
        ce_ma9 = self._calculate_ma(ce_hist, self.ma_period) if not ce_hist.empty else 0
        pe_ma9 = self._calculate_ma(pe_hist, self.ma_period) if not pe_hist.empty else 0
        
        # FALLBACK: If MA9 is 0 (no history), use simple approximation
        # Assume MA9 is ~5% above current price for CE, ~5% below for PE (conservative)
        if ce_ma9 == 0 and ce_close > 0:
            ce_ma9 = ce_close * 1.05  # MA above current = "ABOVE" status by default
            print(f"   ⚠️ CE MA9 estimated (no history)")
        if pe_ma9 == 0 and pe_close > 0:
            pe_ma9 = pe_close * 1.05  # MA above current = "ABOVE" status by default
            print(f"   ⚠️ PE MA9 estimated (no history)")
        
        # If still no data at all, return neutral
        if ce_close == 0 and pe_close == 0:
            print(f"{Fore.RED}❌ No option quotes available. Trend = NEUTRAL{Style.RESET_ALL}")
            self.state_manager.update("trend", "NEUTRAL")
            return "NEUTRAL", atm_strike, {}
        
        print(f"   ATM CE ({ce_symbol}): LTP={ce_close:.2f} | MA9={ce_ma9:.2f}")
        print(f"   ATM PE ({pe_symbol}): LTP={pe_close:.2f} | MA9={pe_ma9:.2f}")

        # 6. Apply Trend Logic
        # Bullish if PE < MA9 (Puts decaying/short covering)
        # Bearish if CE < MA9 (Calls decaying/long unwinding)
        
        trend = "NEUTRAL"
        pe_below = pe_close < pe_ma9 if pe_ma9 > 0 else False
        ce_below = ce_close < ce_ma9 if ce_ma9 > 0 else False
        
        source = "-"
        if pe_below and not ce_below:
            trend = "BULLISH"
            source = pe_symbol
            print(f"{Fore.GREEN}✅ TREND CONFIRMED: BULLISH (PE < MA9){Style.RESET_ALL}")
        elif ce_below and not pe_below:
            trend = "BEARISH"
            source = ce_symbol
            print(f"{Fore.RED}✅ TREND CONFIRMED: BEARISH (CE < MA9){Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}🟡 NO CLEAR DIRECTION (Neutral/Chop){Style.RESET_ALL}")

        details = {
            "ce_symbol": ce_symbol,
            "ce_close": round(ce_close, 2),
            "ce_ma9": round(ce_ma9, 2),
            "pe_symbol": pe_symbol,
            "pe_close": round(pe_close, 2),
            "pe_ma9": round(pe_ma9, 2),
            "pe_below": pe_below,
            "ce_below": ce_below
        }

        # 7. Update Centralized State
        current_state = self.state_manager.get_state().get("ng_strategy_state", {}).copy()
        current_state.update({
            "trend_bias": trend,
            "signal_source": source,
            "engine_heartbeat": datetime.now().timestamp()
        })
        self.state_manager.update("ng_strategy_state", current_state)
        
        # [FIX] Update Root Trend for Dashboard Display
        self.state_manager.update("trend", trend)
        
        engine_state = {
            "ce_leg": {"symbol": ce_symbol, "price": round(ce_close, 2), "ma9": round(ce_ma9, 2), "status": "BELOW" if ce_below else "ABOVE"},
            "pe_leg": {"symbol": pe_symbol, "price": round(pe_close, 2), "ma9": round(pe_ma9, 2), "status": "BELOW" if pe_below else "ABOVE"},
            "signal_source": source,
            "confirmation_time": datetime.now().strftime("%H:%M:%S") if trend != "NEUTRAL" else "-"
        }
        self.state_manager.update("ng_trend_engine", engine_state)
        
        return trend, atm_strike, details
    def check_data_feed_health(self):
        """
        [NEW] Checks if data feed is fresh. Returns True if OK, False if Stale/Error.
        """
        health = self.state_manager.get_state().get("system_health", {})
        last_candle_time = health.get("last_candle", "-")
        
        # If we have no candle time yet, or it's a placeholder, consider it "WAITING"
        if last_candle_time == "-":
            return True 
            
        try:
            # Parse timestamp 'YYYY-MM-DD HH:MM:SS'
            last_dt = datetime.strptime(last_candle_time, "%Y-%m-%d %H:%M:%S")
            now = datetime.now()
            
            # Since these are DAILY candles, being "stale" usually means > 24 hours old
            # during market hours. For now, let's use a 48-hour threshold to be safe
            # against weekends/holidays if we don't have a holiday calendar.
            diff = now - last_dt
            if diff.total_seconds() > 172800: # 48 hours
                self.state_manager.add_alert("CRITICAL", "SYSTEM", "Data Feed Disrupted. Candles are too old.")
                health_copy = health.copy()
                health_copy["data_feed_status"] = "DELAYED"
                self.state_manager.update("system_health", health_copy)
                return False
                
            # If it was delayed but now recovered
            if health.get("data_feed_status") == "DELAYED":
                 health_copy = health.copy()
                 health_copy["data_feed_status"] = "OK"
                 self.state_manager.update("system_health", health_copy)
                 self.state_manager.add_log("✅ Data Feed Resumed", category="SYSTEM", user="SYSTEM")

        except Exception as e:
            self.state_manager.add_log(f"Data Feed Health Check Error: {e}", category="ERROR", user="SYSTEM")
            return False
            
        return True

        # Old run_iteration replaced by analyze_trend
        pass

    def _fetch_history(self, symbol):
        """
        Fetches historical daily data.
        [OPTIMIZED] Uses Class-Level Cache to persist across daily refreshes.
        """
        now = datetime.now().timestamp()
        
        # Check Class-Level Cache
        if symbol in StrategyEngine._history_cache:
            df, ts = StrategyEngine._history_cache[symbol]
            if now - ts < StrategyEngine._history_ttl:
                # print(f"📚 History Cache Hit for {symbol}")
                return df
                
        # Simulation Mode for Demo if Fyers is Offline
        if not self.broker.fyers or self.broker.fyers is None:
            return self._generate_mock_history()

        # Fyers History API parameters
        today = datetime.now().date()
        start_date = today - timedelta(days=30)
        
        data = {
            "symbol": symbol,
            "resolution": "1D",
            "date_format": "1",
            "range_from": start_date.strftime("%Y-%m-%d"),
            "range_to": today.strftime("%Y-%m-%d"),
            "cont_flag": "1"
        }

        try:
            resp = self.broker.fyers.history(data=data)
            if resp.get("s") == "ok":
                candles = resp.get("candles", [])
                df = pd.DataFrame(candles, columns=["epoch", "open", "high", "low", "close", "volume"])
                
                if not df.empty:
                    last_ts = df['epoch'].iloc[-1]
                    # Convert epoch to readable if needed, or just store as is
                    dt_str = datetime.fromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M:%S")
                    health = self.state_manager.get_state().get("system_health", {}).copy()
                    health["last_candle"] = dt_str
                    self.state_manager.update("system_health", health)
                    
                    # [NEW] Save to Class Cache
                    StrategyEngine._history_cache[symbol] = (df, now)
                
                return df
        except Exception as e:
            print(f"History fetch error: {e}")
            
        return pd.DataFrame()

    def _calculate_ma(self, df, period):
        if len(df) < period:
            return 0.0
        return df['close'].rolling(window=period).mean().iloc[-1]

    def _generate_mock_history(self):
        """
        Generates fake history for demo purposes.
        """
        import numpy as np
        dates = pd.date_range(end=datetime.now(), periods=20)
        # Create a trend scenario: decreasing prices for Put (Bullish arg?)
        # Let's simulate a Bullish setup: PE prices dropping below MA.
        # So PE closes should be low.
        data = {
            "close": np.linspace(30, 10, 20) + np.random.normal(0, 1, 20)
        }
        return pd.DataFrame(data, index=dates)

    def check_risk_adjustment(self, current_trend, expiry):
        """
        Checks for First-Level Adjustment (Risk Scaling).
        Trigger: Option sustains below MA9 for 6 continuous hours.
        """
        from datetime import datetime
        from src.state_manager import StateManager
        from colorama import Fore, Style
        from src.utils import get_atm_strike, construct_symbol
        
        state_mgr = StateManager.get_instance()
        state = state_mgr.get_state()
        
        # 1. Identify Target Instrument based on Position Trend
        target_opt_type = ""
        if current_trend == "BULLISH":
            target_opt_type = "CE" 
        elif current_trend == "BEARISH":
            target_opt_type = "PE"
        else:
            return False, "Neutral View"

        # 2. Get Current ATM and Prices
        fut_price = self.market_data._get_ltp(self.market_data.fut_symbol)
        atm_strike = get_atm_strike(fut_price, self.market_data.symbol_base)
        
        symbol = construct_symbol(self.market_data.symbol_base, "OPT", expiry, atm_strike, target_opt_type)
        
        # 3. Get MA9 (Daily)
        # Check if we already calculated it? We can fetch history again.
        hist_df = self._fetch_history(symbol)
        ma9 = self._calculate_ma(hist_df, 9)
        ltp = self.market_data._get_ltp(symbol)
        
        print(f"   [ADJ CHECK] {symbol}: LTP={ltp} | MA9={ma9:.2f}")

        # 4. Check Condition (LTP < MA9)
        # 4. Check Condition (LTP < MA9)
        context = state.get("adjustment_context", {}).copy()
        
        # [NEW] Admin Config
        admin_conf = state.get("admin_config", {})
        sustain_limit = admin_conf.get("sustain_time_hours", 6.0)
        
        if ltp < ma9:
            # Condition Met. Check Timer.
            start_time_str = state_mgr.get_state(self.market_data.symbol_base).get("adjustment_start_time")
            if not start_time_str:
                # Start Timer
                now_str = datetime.now().isoformat()
                state_mgr.update("adjustment_start_time", now_str, symbol=self.market_data.symbol_base)
                state_mgr.add_log(f"⚠️ [{self.market_data.symbol_base}] Adjustment Timer Started: {symbol} < MA9")
                
                context.update({
                    "trigger_reason": f"Sustained breach detected",
                    "trigger_source": symbol,
                    "sustenance_timer": f"0.00h / {sustain_limit:.2f}h",
                    "progress_percent": 0,
                    "tracking_label": f"{symbol} < MA9 (Daily)", # [NEW]
                    "start_time_display": datetime.now().strftime("%H:%M IST") # [NEW]
                })
                state_mgr.update("adjustment_context", context)
                
                # [NEW] Alert
                state_mgr.add_alert("WARNING", "ADJUSTMENT", f"Risk Scaling Armed: {symbol} < MA9. Timer Started.")
                
                print(f"{Fore.YELLOW}⚠️  [{self.market_data.symbol_base}] Adjustment Monitor: Timer Started.{Style.RESET_ALL}")
                return False, "Timer Started"
            else:
                # Check Duration
                start_time = datetime.fromisoformat(start_time_str)
                duration = datetime.now() - start_time
                duration_hours = duration.total_seconds() / 3600
                progress = min(100, round((duration_hours / sustain_limit) * 100))
                
                context.update({
                    "sustenance_timer": f"{duration_hours:.2f}h / {sustain_limit:.2f}h",
                    "progress_percent": progress
                })
                state_mgr.update("adjustment_context", context)
                
                print(f"      Sustained Duration: {duration_hours:.2f} Hours")
                
                print(f"      Sustained Duration: {duration_hours:.2f} Hours")
                
                if duration_hours >= sustain_limit:
                     # TRIGGER ADJUSTMENT
                     
                     # 1. Duplicate Check (One Alert Per Day)
                     today_str = datetime.now().strftime("%Y-%m-%d")
                     last_alert = context.get("last_alert_date")
                     
                     if last_alert != today_str:
                         # 2. Construct Message
                         if current_trend == "BULLISH":
                             msg = (
                                 "⚠️ ADJUSTMENT ALERT – BULLISH TRADE\n\n"
                                 "ATM CE has crossed below MA9 and sustained below it.\n\n"
                                 "Benchmark rule triggered:\n"
                                 "→ Exit 2 NG Mini Futures\n"
                                 "→ Futures: 5 → 3\n"
                                 "→ Hedge (5) and Iron Fly (2) remain unchanged\n\n"
                                 "Action required: CONFIRM adjustment."
                             )
                         elif current_trend == "BEARISH":
                             msg = (
                                 "⚠️ ADJUSTMENT ALERT – BEARISH TRADE\n\n"
                                 "ATM PE has crossed below MA9 and sustained below it.\n\n"
                                 "Benchmark rule triggered:\n"
                                 "→ Exit 2 NG Mini Futures\n"
                                 "→ Futures: 5 → 3\n"
                                 "→ Hedge (5) and Iron Fly (2) remain unchanged\n\n"
                                 "Action required: CONFIRM adjustment."
                             )
                         else:
                             msg = f"⚠️ ADJUSTMENT ALERT: {symbol} sustained below MA9."

                         # 3. Send Alert
                         from src.notification_service import send_telegram_alert
                         send_telegram_alert(msg)
                         
                         # 4. Update State
                         context["last_alert_date"] = today_str
                         state_mgr.update("adjustment_context", context)
                         state_mgr.add_log(f"📲 Telegram Alert Sent for {symbol}")

                     state_mgr.add_alert("CRITICAL", "SCALING", f"Scale Down Triggered: {sustain_limit}h Sustain Complete on {symbol}")
                     return True, "TRIGGERED"
                else:
                     return False, f"Waiting ({duration_hours:.1f}/{sustain_limit:.1f} Hrs)"
            
        # Default Safe State construction
        why_safe = []
        
        # Check specific contra-trend conditions that WOULD trigger risk
        if current_trend == "BULLISH":
             # We are safe if ATM CE > MA9 (No bearish pressure)
             # And ATM PE < MA9 (Bullish trend intact) - wait, risk is if trend fails.
             # Actually, risk scaling happens if Price < MA9.
             # So we are SAFE if Price > MA9.
             why_safe.append(f"✔ {symbol} > MA9 (No breakdown)")
             why_safe.append("✔ Bullish Trend Intact")
        elif current_trend == "BEARISH":
             why_safe.append(f"✔ {symbol} > MA9 (No breakdown)")
             why_safe.append("✔ Bearish Trend Intact")
             
        why_safe.append("✔ No 6-hour sustain detected")

        # [FIX] Clear Timer if Safe
        state_mgr.update("adjustment_start_time", None, symbol=self.market_data.symbol_base)

        context.update({
            "trigger_reason": "SAFE",
            "trigger_source": "-",
            "sustenance_timer": f"0.00h / {sustain_limit:.2f}h",
            "progress_percent": 0,
            "safety_checklist": why_safe,   # [NEW]
            "tracking_label": "-"           # [NEW]
        })
        state_mgr.update("adjustment_context", context)
                
        print(f"{Fore.GREEN}ℹ️  [{self.market_data.symbol_base}] Adjustment Monitor: Safe. {why_safe}{Style.RESET_ALL}")
            
        return False, "Safe"

    def check_observation_exit(self, current_trend, expiry):
        """
        Level 2 Decision: After scaling to 3 lots, enters OBSERVATION MODE.
        
        Rules:
        1. Mandatory wait: 1 full trading day after scaling
        2. Check if opposing option signal stabilizes or worsens
        3. If stabilizes: Hold remaining 3 futures
        4. If worsens: Exit remaining futures (requires confirmation)
        """
        from datetime import datetime, timedelta
        from src.state_manager import StateManager
        from src.utils import get_atm_strike, construct_symbol
        from colorama import Fore, Style
        
        state_mgr = StateManager.get_instance()
        state = state_mgr.get_state()
        
        # Only check if we're in SCALED phase
        inst_state = state_mgr.get_state(self.market_data.symbol_base)
        if inst_state.get("strategy_phase") != "SCALED":
            return False, "Not in Observation Mode"
        
        # 1. MANDATORY WAIT RULE: 1 full trading day
        inst_state = state_mgr.get_state(self.market_data.symbol_base)
        adjustment_time_str = inst_state.get("adjustment_start_time")
        
        context = state.get("observation_context", {}).copy()
        
        if not adjustment_time_str:
            context.update({"countdown": "Waiting for scaling", "is_locked": False})
            state_mgr.update("observation_context", context)
            return False, "No adjustment timestamp"
        
        adjustment_time = datetime.fromisoformat(adjustment_time_str)
        adjustment_time = datetime.fromisoformat(adjustment_time_str)
        
        # [NEW] Admin Config for days
        admin_conf = state.get("admin_config", {})
        obs_days = admin_conf.get("observation_period_days", 1)
        
        lock_expiry = adjustment_time + timedelta(days=obs_days)
        time_since_scaling = datetime.now() - adjustment_time
        hours_since_scaling = time_since_scaling.total_seconds() / 3600
        
        # Require full period
        obs_hours = obs_days * 24
        if hours_since_scaling < obs_hours:
            remaining = obs_hours - hours_since_scaling
            context.update({
                "lock_expiry": lock_expiry.strftime("%H:%M (%d %b)"),
                "countdown": f"{remaining:.1f}h remaining",
                "is_locked": True,
                "action_allowed": "NONE (Locked)" # [NEW] Explicit permission
            })
            state_mgr.update("observation_context", context)
            
            # [NEW] One-time alert for Lock
            if not context.get("lock_alert_sent", False):
                 state_mgr.add_alert("CRITICAL", "OBSERVATION", f"Re-entry Locked until {lock_expiry.strftime('%H:%M')}. Observation Mode Active.")
                 # (In real app, we'd need a flag to prevent spam, simplistic here)
            
            return False, f"Observation Wait: {remaining:.1f}h remaining"
        
        context.update({
            "countdown": "Window Open", 
            "is_locked": False,
            "action_allowed": "EXIT ONLY"  # [NEW] Explicit permission
        })
        state_mgr.update("observation_context", context)
        
        # 2. Check opposing option behavior
        target_opt_type = ""
        if current_trend == "BULLISH":
            target_opt_type = "CE"
        elif current_trend == "BEARISH":
            target_opt_type = "PE"
        else:
            return False, "Neutral - No observation needed"
        
        fut_price = self.market_data._get_ltp(self.market_data.fut_symbol)
        atm_strike = get_atm_strike(fut_price, self.market_data.symbol_base)
        symbol = construct_symbol(self.market_data.symbol_base, "OPT", expiry, atm_strike, target_opt_type)
        
        # 3. Fetch recent history to check pattern
        hist_df = self._fetch_history(symbol)
        if hist_df.empty or len(hist_df) < 3:
            return False, "Insufficient history"
        
        ma9 = self._calculate_ma(hist_df, 9)
        ltp = self.market_data._get_ltp(symbol)
        
        # Get last 3 closes
        recent_closes = hist_df['close'].tail(3).tolist()
        
        print(f"\n{Fore.CYAN}📊 OBSERVATION MODE CHECK{Style.RESET_ALL}")
        print(f"   {symbol}: LTP={ltp:.2f} | MA9={ma9:.2f}")
        print(f"   Recent closes: {[f'{c:.2f}' for c in recent_closes]}")
        
        # 4. CASE 1: Signal Stabilizes
        is_stabilizing = True
        
        # Check if making lower lows
        if len(recent_closes) >= 3:
            if recent_closes[-1] < recent_closes[-2] < recent_closes[-3]:
                is_stabilizing = False
        
        # Check if recovering toward MA9
        if ltp < ma9 * 0.95:  # Still >5% below MA9
            is_stabilizing = False
        
        if is_stabilizing:
            print(f"{Fore.GREEN}✅ SIGNAL STABILIZING: Hold 3 futures{Style.RESET_ALL}")
            return False, "Stabilized - Hold"
        
        # 5. CASE 2: Signal Worsens → Exit remaining
        print(f"{Fore.RED}⚠️ SIGNAL WORSENING: Exit recommended{Style.RESET_ALL}")
        state_mgr.add_alert("CRITICAL", "EXIT", "Trend Against Trade Confirmed. Exit Remaining Futures Recommended.")
        return True, "Deteriorating - Exit"


