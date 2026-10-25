import sys
import time
import threading
from datetime import datetime
from colorama import init, Fore, Style
from src.state_manager import StateManager
from src.broker import FyersConnector
from src.market_data import MarketDataEngine
from src.strategy import StrategyEngine
from src.execution import ExecutionEngine
from src.order_executor import OrderExecutor  # [NEW] Sequential Order Executor
from src.trade_manager import TradeManager
from src.utils import get_atm_strike

from src.token_manager import TokenManager
from src.web_server import run_web_server
from src.strategy_state import StrategyStateEngine, StrategyState
from src.order_intent import OrderIntentEngine
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass # Python < 3.7 fallback

from src.positions_analyzer import PositionsAnalyzer
from src.adjustment_engine import AdjustmentEngine
from src.config import ON_DEMAND_MODE, MOBILE_PUSH_POLL_SECONDS, is_market_open, get_ist_time

init(autoreset=True)

class TradingBot:
    def __init__(self):
        # [NEW] Auto-Login / Token Validation
        print(f"{Fore.CYAN}🔐 Validating Session...{Style.RESET_ALL}")
        TokenManager.get_instance().ensure_valid_token()

        self.state_mgr = StateManager.get_instance()
        self.broker = FyersConnector()
        self.engines = {}
        self.running = True

    def initialize_broker(self):
        if not self.broker.connect():
            print(f"{Fore.YELLOW}⚠️  Broker Connection Failed. Using Simulation Mode.{Style.RESET_ALL}")
            return False
        return True

    def run_instrument_cycle(self, inst_config):
        symbol = inst_config['symbol']
        lot_size = inst_config['lot_size']

        try:
            # Lazy init engines for each symbol
            if symbol not in self.engines:
                market = MarketDataEngine(self.broker, symbol_base=symbol)
                strategy = StrategyEngine(self.broker, market)
                executor = ExecutionEngine(self.broker, market, symbol_base=symbol, lot_size=lot_size)
                order_executor = OrderExecutor(self.broker, symbol_base=symbol)  # [NEW]
                trade_manager = TradeManager(self.broker)

                self.engines[symbol] = {
                    "market": market,
                    "strategy": strategy,
                    "executor": executor,
                    "order_executor": order_executor,  # [NEW]
                    "trade_manager": trade_manager
                }

            eng = self.engines[symbol]
            market = eng["market"]
            strategy = eng["strategy"]
            executor = eng["executor"]
            trade_mgr = eng["trade_manager"]

            # 1. Update Basic Market Context
            fut_symbol = market.fut_symbol
            expiry = market.expiry_suffix

            # 2. Position Monitoring
            positions = [p for p in self.broker.get_positions() if symbol in p.get('symbol', '')]
            total_pnl = sum([float(p.get('pl', 0)) for p in positions])

            self.state_mgr.update("positions", positions, symbol=symbol)
            self.state_mgr.update("total_pnl", total_pnl, symbol=symbol)

            # 3. Price & ATM Update
            market_price = market._get_ltp(fut_symbol)
            if market_price == 0:
                print(f"{Fore.RED}❌ [{symbol}] Failed to fetch Market Price.{Style.RESET_ALL}")
                return

            atm_strike = get_atm_strike(market_price, symbol)

            self.state_mgr.update("market_price", market_price, symbol=symbol)
            self.state_mgr.update("expiry", expiry, symbol=symbol)
            self.state_mgr.update("atm_strike", atm_strike, symbol=symbol)

            print(f"[{symbol}] 📊 CMP: {market_price} | ATM: {atm_strike} | PnL: ₹{total_pnl:,.2f}")


            # 4. Strategy Analysis
            try:
                trend, _, details = strategy.analyze_trend(fut_symbol, expiry)
                print(f"{Fore.CYAN}DEBUG [bot.py line 95]: Trend analyzed = {trend}{Style.RESET_ALL}")
            except Exception as e:
                print(f"{Fore.RED}❌ Strategy Analysis Error: {e}{Style.RESET_ALL}")
                trend = "NEUTRAL"
                details = {}

            self.state_mgr.update("trend", trend, symbol=symbol)
            self.state_mgr.update("analysis_details", details, symbol=symbol)

            # [FIX] Fetch latest state for logic usage
            inst_state = self.state_mgr.get_state(symbol)
            exec_state = inst_state.get("order_execution_state", {})

            # ==========================================
            # STRATEGY STATE ENGINE INTEGRATION
            # Trend is INPUT, Strategy State is CONTROL
            # ==========================================

            strategy_engine = StrategyStateEngine.get_instance()
            intent_engine = OrderIntentEngine.get_instance(self.broker)

            # Count sustained candles in same trend direction
            prev_trend = inst_state.get("previous_trend", "NEUTRAL")
            sustained = inst_state.get("trend_sustained_candles", 0)

            # Persist background-engine observations as well as on-demand
            # dashboard refreshes. This is audit-only and does not alter FYERS.
            try:
                from src.database import UnifiedOrderManager
                db = UnifiedOrderManager.get_instance()
                payload = {"previous_trend": prev_trend, "trend_changed": prev_trend != trend,
                           "details": details, "sustained_candles": sustained}
                db.record_strategy_event("TREND_ANALYSIS", symbol=symbol, trend=trend,
                                         signal_status=details.get("signal_status"),
                                         futures_confirmation=details.get("futures_confirmation"),
                                         expiry=expiry, atm_strike=atm_strike,
                                         market_price=market_price, payload=payload)
                if details.get("futures_confirmation"):
                    db.record_strategy_event("FUTURES_CONFIRMATION", symbol=symbol, trend=trend,
                                             signal_status=details.get("signal_status"),
                                             futures_confirmation=details.get("futures_confirmation"),
                                             expiry=expiry, atm_strike=atm_strike,
                                             market_price=market_price,
                                             payload={"confirmation": details.get("futures_confirmation")})
                if prev_trend != trend:
                    db.record_strategy_event("TREND_CHANGE", symbol=symbol, trend=trend,
                                             signal_status=details.get("signal_status"),
                                             futures_confirmation=details.get("futures_confirmation"),
                                             expiry=expiry, atm_strike=atm_strike,
                                             market_price=market_price,
                                             payload={"previous_trend": prev_trend, "new_trend": trend})
            except Exception as event_error:
                print(f"⚠️ Strategy event persistence failed: {event_error}")

            if trend == prev_trend and trend in ["BULLISH", "BEARISH"]:
                sustained += 1
            else:
                sustained = 1 if trend in ["BULLISH", "BEARISH"] else 0

            self.state_mgr.update("previous_trend", trend, symbol=symbol)
            self.state_mgr.update("trend_sustained_candles", sustained, symbol=symbol)

            # Process trend signal through Strategy State Engine
            state_action = strategy_engine.process_trend_signal(trend, sustained)

            if state_action.get("action") == "CREATE_ORDER_INTENT":
                # Strategy State approved - create Order Intent (draft order)
                intent_result = intent_engine.create_intent(
                    direction=trend,
                    basket_type="ENTRY",
                    trigger_rule=f"Trend {trend} sustained for {sustained} candles",
                    atm_strike=atm_strike,
                    expiry=expiry,
                    strategy_name=f"Strategy 1 — {trend.title()} Trending Markets"
                )

                if intent_result.get("success"):
                    print(f"{Fore.GREEN}✅ Order Intent Created: {intent_result.get('intent', {}).get('intent_id')}{Style.RESET_ALL}")
                    self.state_mgr.add_log(f"📋 [{symbol}] Order Intent ready for validation")

            elif state_action.get("action") == "CANCEL_INTENT":
                # Trend lost before execution - cancel pending intent
                intent_engine.cancel_intent("Trend became neutral before execution")

            # Consolidation is a valid Strategy 2 basket when both ATM legs
            # closed below MA9 and futures price action confirms a range.
            if (trend == "NEUTRAL" and details.get("both_below_ma9") and
                    details.get("futures_confirmation") == "CONSOLIDATION" and
                    not intent_engine.get_current_intent()):
                intent_result = intent_engine.create_intent(
                    direction="NEUTRAL", basket_type="ENTRY",
                    trigger_rule="Both ATM options below MA9 with confirmed futures consolidation",
                    atm_strike=atm_strike, expiry=expiry,
                    strategy_name="Strategy 2 — Consolidation Markets"
                )
                if intent_result.get("success"):
                    self.state_mgr.add_log(f"📋 [{symbol}] Strategy 2 consolidation basket ready for validation")

            # ==========================================
            # POSITIONS ANALYZER (For IN_POSITION state)
            # ==========================================
            current_strategy_state = strategy_engine.get_current_state()
            if current_strategy_state.get("current_state") in [StrategyState.IN_POSITION.value, StrategyState.ADJUSTMENT_ALLOWED.value]:
                analyzer = PositionsAnalyzer(self.broker)
                analysis = analyzer.analyze(symbol)

                # Run adjustment check
                adj_engine = AdjustmentEngine(self.broker, symbol)
                adj_result = adj_engine.check_adjustments()

                if adj_result.get("needs_adjustment"):
                    adj_engine.trigger_adjustment(
                        adj_result.get("action"),
                        f"Rule: {adj_result.get('triggered_rule')}"
                    )

            # ==========================================
            # END STRATEGY STATE ENGINE INTEGRATION
            # ==========================================

            # 5. Risk Monitoring (TradeManager)
            # This checks global risk and can trigger exit_all via executor
            trade_mgr.monitor_positions(executor=executor)

            # Legacy flow - keeping for backward compatibility
            # Entry Basket Creation (now handled by Order Intent Engine above)
            workflow = self.state_mgr.get_workflow_state()
            current_wf = workflow.get("state", "NO_TRADE")
            exec_status = exec_state.get("status", "IDLE") if exec_state else "IDLE"

            # Skip legacy basket creation if intent engine is handling it
            current_intent = intent_engine.get_current_intent()
            has_pending_intent = current_intent and current_intent.get("status") in ["DRAFT", "VALIDATED"]
            has_approval = inst_state.get("trade_approval") is True

            # Legacy flow now handled by Order Intent Engine above

            # Handle manual approval for any basket type
            if has_approval and exec_state.get("status") == "PENDING":
                basket_id = exec_state.get("basket_id")
                basket_type = exec_state.get("basket_type", "ENTRY")

                if basket_id:
                    print(f"{Fore.GREEN}🚀 User approved {basket_type}! Executing basket: {basket_id}{Style.RESET_ALL}")

                    # Clear approval flag
                    self.state_mgr.update("trade_approval", False, symbol=symbol)
                    self.state_mgr.update("pending_signal", None, symbol=symbol)

                    # Transition workflow to EXECUTING
                    self.state_mgr.transition_workflow_state("EXECUTING" if basket_type == "ENTRY" else "ADJUSTMENT_EXECUTING")

                    # Execute basket in background thread
                    order_executor = self.engines[symbol]['order_executor']
                    def execute_in_bg():
                        success = order_executor.execute_basket(basket_id)
                        if success:
                            if basket_type == "ENTRY":
                                self.state_mgr.transition_workflow_state("POSITIONS_ACTIVE")
                                self.state_mgr.update("strategy_phase", "POSITIONS_ACTIVE", symbol=symbol)
                            elif basket_type == "ADJUSTMENT":
                                self.state_mgr.transition_workflow_state("OBSERVATION")
                                self.state_mgr.update("strategy_phase", "SCALED", symbol=symbol)
                            elif basket_type == "EXIT":
                                self.state_mgr.transition_workflow_state("NO_TRADE")
                                self.state_mgr.update("strategy_phase", "NO_TRADE", symbol=symbol)
                        else:
                            self.state_mgr.transition_workflow_state("NO_TRADE") # Reset on fail

                    thread = threading.Thread(target=execute_in_bg, daemon=True)
                    thread.start()

        except Exception as e:
            print(f"{Fore.RED}❌ ERROR [{symbol}]: {e}{Style.RESET_ALL}")

    def main_loop(self):
        print(f"{Fore.CYAN}🚀 STARTING NG (NATURAL GAS) TRADING BOT{Style.RESET_ALL}")
        self.initialize_broker()

        # Start web server in background
        threading.Thread(target=run_web_server, daemon=True).start()

        # Check ON_DEMAND_MODE
        if ON_DEMAND_MODE:
            print(f"{Fore.GREEN}📊 ON-DEMAND MODE ENABLED - API calls only on user refresh{Style.RESET_ALL}")
            print(f"{Fore.GREEN}🌐 Dashboard available at http://localhost:5000{Style.RESET_ALL}")
        else:
            print("DEBUG: Web Server Thread Started. Entering Main Loop...")

        token_mgr = TokenManager()
        last_token_check = time.time()
        last_mobile_push_poll = 0

        while self.running:
            try:
                if ON_DEMAND_MODE and is_market_open() and time.time() - last_mobile_push_poll >= MOBILE_PUSH_POLL_SECONDS:
                    last_mobile_push_poll = time.time()
                    from src.mobile_service import has_registered_devices
                    if has_registered_devices():
                        print("📱 Checking market trend for registered mobile push devices...")
                        from src.web_server import app, refresh_market_data
                        with app.test_request_context('/api/refresh', method='POST', json={"symbol": "NATGASMINI"}):
                            refresh_market_data()

                # Delayed email events are checked without triggering market API polling.
                from src.notification_service import process_due_trend_notification, process_state_alert_notifications
                process_due_trend_notification(self.state_mgr)
                process_state_alert_notifications(self.state_mgr)

                # [NEW] Market Hours Check (IST)
                if not is_market_open():
                    ist_now = get_ist_time().strftime("%H:%M:%S")
                    print(f"🌙 Market Closed ({ist_now} IST). Sleeping...")
                    self.state_mgr.update("status", "MARKET_CLOSED")
                    time.sleep(60) # Sleep 1 minute
                    continue

                # Periodic Token Validation (Every 15 mins) - API call only here
                if time.time() - last_token_check > 900:
                    last_token_check = time.time()

                    # Update Broker Connection Status in State (only every 15 mins)
                    is_connected = token_mgr.validate_token()
                    status_str = "CONNECTED" if is_connected else "DISCONNECTED"

                    health = self.state_mgr.get_state().get("system_health", {})
                    if health.get("broker_connection") != status_str:
                         health["broker_connection"] = status_str
                         self.state_mgr.update("system_health", health)
                         if not is_connected:
                             self.state_mgr.add_log("⚠️ Broker Disconnected - Attempting Reconnect...", category="SYSTEM")

                    if token_mgr.is_token_expiring_soon(hours=2):
                        print(f"{Fore.YELLOW}⚠️ Token expiring soon. Refreshing...{Style.RESET_ALL}")
                        new_token = token_mgr.ensure_valid_token()
                        if new_token:
                             # Re-connect broker with new token
                             print(f"{Fore.GREEN}🔄 Updating Broker Session with new Token...{Style.RESET_ALL}")
                             self.broker.access_token = new_token
                             self.broker.connect()

                # Check System Status
                sys_state = self.state_mgr.get_state()
                if sys_state.get("status") == "STOPPED":
                    time.sleep(5)
                    continue

                self.state_mgr.update("status", "RUNNING")

                # ON-DEMAND MODE: Skip continuous API polling
                if ON_DEMAND_MODE:
                    # In on-demand mode, just keep the server alive
                    # Data is only fetched when user refreshes the dashboard
                    time.sleep(30)  # Longer sleep since we're not polling
                    continue

                # CONTINUOUS MODE (Original behavior)
                # Single Instrument: NATGASMINI
                ng_config = {
                    "symbol": "NATGASMINI",
                    "lot_size": 1250
                }

                print(f"{Fore.BLUE}🔄 Starting Cycle for NATGASMINI...{Style.RESET_ALL}")
                self.run_instrument_cycle(ng_config)

                print(f"{Fore.CYAN}💤 Cycle Complete. Sleeping 15s...{Style.RESET_ALL}")
                time.sleep(15)

            except KeyboardInterrupt:
                print(f"{Fore.YELLOW}🛑 Bot Stopping...{Style.RESET_ALL}")
                self.running = False
            except Exception as e:
                print(f"{Fore.RED}Critical Loop Error: {e}{Style.RESET_ALL}")
                time.sleep(10)

if __name__ == "__main__":
    bot = TradingBot()
    bot.main_loop()
