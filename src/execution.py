from datetime import datetime
from colorama import Fore, Style
from src.utils import construct_symbol, SYMBOL_SPECS

from src.state_manager import StateManager

class ExecutionEngine:
    def __init__(self, broker, market_data, symbol_base="NATGASMINI", lot_size=1250):
        self.broker = broker
        self.market = market_data
        self.symbol_base = symbol_base
        self.lot_size = lot_size
        self.state_manager = StateManager.get_instance()

    def preview_basket(self, trend, atm_strike, expiry_suffix):
        """
        Returns a list of order objects with Live LTPs based on the Strategy.
        """
        specs = SYMBOL_SPECS.get(self.symbol_base, SYMBOL_SPECS["NATGASMINI"])
        hedge_offset = specs["hedge_offset"]
        wing_dist = specs["wing_dist"]

        basket = []
        if trend not in ["BULLISH", "BEARISH"]:
            return basket
            
        # [NEW] Admin Limits
        # from src.state_manager import StateManager # Removed, now self.state_manager
        # state_mgr = StateManager.get_instance() # Removed, now self.state_manager
        admin_conf = self.state_manager.get_state().get("admin_config", {})
        max_qty = admin_conf.get("max_futures_qty", 5)

        # 1. FUT Symbol
        fut_symbol = construct_symbol(self.symbol_base, "FUT", expiry_suffix)
        fut_ltp = self.market._get_ltp(fut_symbol)

        if trend == "BULLISH":
            # A. Directional: Base 5 lots for NG
            basket.append({
                "leg": "FUT (Buy)",
                "symbol": fut_symbol,
                "side": "BUY",
                "qty": max_qty,
                "ltp": fut_ltp,
                "value": 0
            })

            # B. Hedge: ITM PE
            hedge_strike = atm_strike + hedge_offset
            hedge_sym = construct_symbol(self.symbol_base, "OPT", expiry_suffix, hedge_strike, "PE")
            hedge_ltp = self.market._get_ltp(hedge_sym)

            basket.append({
                "leg": f"Hedge PE {hedge_strike}",
                "symbol": hedge_sym,
                "side": "BUY",
                "qty": max_qty,
                "ltp": hedge_ltp,
                "value": 0
            })

        elif trend == "BEARISH":
            # A. Directional: Base 5 lots
            basket.append({
                "leg": "FUT (Sell)",
                "symbol": fut_symbol,
                "side": "SELL",
                "qty": max_qty,
                "ltp": fut_ltp,
                "value": 0
            })

            # B. Hedge: ITM CE
            hedge_strike = atm_strike - hedge_offset
            hedge_sym = construct_symbol(self.symbol_base, "OPT", expiry_suffix, hedge_strike, "CE")
            hedge_ltp = self.market._get_ltp(hedge_sym)

            basket.append({
                "leg": f"Hedge CE {hedge_strike}",
                "symbol": hedge_sym,
                "side": "BUY",
                "qty": max_qty,
                "ltp": hedge_ltp,
                "value": 0
            })

        # C. Iron Fly (Always part of entry)
        ce_atm_sym = construct_symbol(self.symbol_base, "OPT", expiry_suffix, atm_strike, "CE")
        pe_atm_sym = construct_symbol(self.symbol_base, "OPT", expiry_suffix, atm_strike, "PE")
        ce_wing_sym = construct_symbol(self.symbol_base, "OPT", expiry_suffix, atm_strike + wing_dist, "CE")
        pe_wing_sym = construct_symbol(self.symbol_base, "OPT", expiry_suffix, atm_strike - wing_dist, "PE")
        
        basket.append({"leg": f"Iron Fly: Sell ATM CE", "symbol": ce_atm_sym, "side": "SELL", "qty": 2, "ltp": self.market._get_ltp(ce_atm_sym), "value": 0})
        basket.append({"leg": f"Iron Fly: Sell ATM PE", "symbol": pe_atm_sym, "side": "SELL", "qty": 2, "ltp": self.market._get_ltp(pe_atm_sym), "value": 0})
        basket.append({"leg": f"Iron Fly: Buy Wing CE", "symbol": ce_wing_sym, "side": "BUY", "qty": 2, "ltp": self.market._get_ltp(ce_wing_sym), "value": 0})
        basket.append({"leg": f"Iron Fly: Buy Wing PE", "symbol": pe_wing_sym, "side": "BUY", "qty": 2, "ltp": self.market._get_ltp(pe_wing_sym), "value": 0})

        # Update values
        for b in basket:
             if b["value"] == 0:
                  # All legs now use actual lot count (qty)
                  b["value"] = (b["ltp"] or 0) * b["qty"] * self.lot_size

        return basket

    def exit_all_positions(self):
        """
        Closes all open positions for this specific instrument.
        """
        print(f"\n{Fore.RED}🚨 EXECUTING EXIT ALL [{self.symbol_base}]{Style.RESET_ALL}")
        positions = self.broker.get_positions()
        from src.config import DRY_RUN
        # from src.state_manager import StateManager # Removed, now self.state_manager
        
        for pos in positions:
            symbol = pos.get('symbol', '')
            if self.symbol_base in symbol:
                net_qty = int(pos.get('netQty', 0))
                if net_qty != 0:
                    print(f"   CLOSE [{self.symbol_base}]: {symbol} (Qty: {net_qty})")
                    if not DRY_RUN:
                        self.broker.close_position(symbol, abs(net_qty))
                    else:
                        print(f"      [DRY RUN] Exit skipped.")
        
        self.state_manager.update("strategy_phase", "EXITED", symbol=self.symbol_base)

    def reduce_futures_exposure(self, qty):
        """
        Scales down futures exposure by closing positions.
        """
        self.state_manager.add_log(f"📉 SCALING: Reducing Futures Exposure by {qty} lots...")
        
        # 1. Identify Futures Position
        positions = self.state_manager.state.get("positions", [])
        fut_pos = next((p for p in positions if "FUT" in p.get("symbol", "")), None)
        
        if not fut_pos:
             self.state_manager.add_log("Scaling Skipped: No Futures Position Found.")
             return

        symbol = fut_pos.get("symbol")
        net_qty = int(fut_pos.get("netQty", 0))
        
        if abs(net_qty) < qty:
            self.state_manager.add_log(f"Scaling Warning: Current Qty {net_qty} < Scale Qty {qty}. Closing All.")
            qty = abs(net_qty)
            
        # 2. Execute Close Order
        # In DRY RUN, we just log. In LIVE, we call broker.
        from src.config import DRY_RUN
        
        if not DRY_RUN:
             order_id = self.broker.close_position(symbol, qty)
             if order_id:
                self.state_manager.add_log(f"✅ Scale Down Executed: Closed {qty}x {symbol} (ID: {order_id})")
                
                # Update State Machine
                sm_state = self.state_manager.state.get("strategy_machine", {}).copy()
                sm_state.update({"state": "SCALING", "level": 3, "status": "WAITING"})
                self.state_manager.update("strategy_machine", sm_state)
                
                self.state_manager.add_log(f"📉 [{self.market.symbol_base}] Scaled Down {qty} lots")
        else:
            print(f"      [DRY RUN] Scale Down skipped.")
            self.state_manager.update("strategy_phase", "SCALED", symbol=self.symbol_base)
            sm_state = self.state_manager.state.get("strategy_machine", {}).copy()
            sm_state.update({"state": "SCALING", "level": 3, "status": "WAITING"})

    def check_daily_loss_limit(self):
        """
        [NEW] Checks if Daily PnL exceeds the Admin-defined Max Loss %
        """
        state = self.state_manager.get_state()
        config = state.get("admin_config", {})
        max_loss_pct = float(config.get("max_daily_loss_pct", 2.0))
        
        # Use Capital from Fund State (Real or Mock)
        fund_state = state.get("fund_risk_state", {})
        capital = fund_state.get("capital", 500000.0)
        
        max_loss_amount = capital * (max_loss_pct / 100.0)
        
        current_pnl = state.get("total_pnl", 0.0)
        
        if current_pnl < -abs(max_loss_amount):
             self.state_manager.add_alert("CRITICAL", "RISK", f"Daily Loss Limit Breached: {current_pnl} < -{max_loss_amount}")
             return False # Block Trading
        
        return True

    def execute_logic(self, trend, atm_strike, expiry_suffix):
        print(f"{Fore.MAGENTA}DEBUG: execute_logic called with trend={trend}, atm_strike={atm_strike}, expiry={expiry_suffix}{Style.RESET_ALL}")
        
        if trend == "NEUTRAL": 
            print(f"{Fore.YELLOW}DEBUG: Trend is NEUTRAL, returning{Style.RESET_ALL}")
            return
        
        # [NEW] Check Daily Loss Limit (Hard Block)
        if not self.check_daily_loss_limit():
            print(f"{Fore.RED}⛔ EXECUTION BLOCKED: Daily Loss Limit Breached.{Style.RESET_ALL}")
            return
            
        # [DISABLED] Check Risk Status - Orders should be sent even with low balance
        # Let Fyers reject the orders if insufficient funds
        # risk_state = self.state_manager.get_state().get("fund_risk_state", {})
        # if risk_state.get("risk_status") == "RISK_LOCK":
        #      print(f"{Fore.RED}⛔ EXECUTION BLOCKED: RISK LOCK ACTIVE (>90% Margin Used){Style.RESET_ALL}")
        #      return

        from src.config import REQUIRE_ENTRY_CONFIRMATION
        
        print(f"{Fore.CYAN}DEBUG: REQUIRE_ENTRY_CONFIRMATION = {REQUIRE_ENTRY_CONFIRMATION}{Style.RESET_ALL}")
        
        # Re-import StateManager/state_mgr as it might not be in scope if not self
        state_mgr = self.state_manager
        is_auto = state_mgr.get_state().get("autonomous_mode", False)
        
        print(f"{Fore.CYAN}DEBUG: is_auto = {is_auto}{Style.RESET_ALL}")
        
        # Risk Check: Block entry if Observation Lock is Active
        obs_context = state_mgr.get_state().get("observation_context", {})
        if obs_context.get("is_locked"):
            print(f"{Fore.RED}⛔ ENTRY BLOCKED: Observation Mode Re-entry Lock Active.{Style.RESET_ALL}")
            return
        
        # [NEW] Check Emergency Halt (Automated)
        emer = state_mgr.get_state().get("emergency_controls", {})
        if emer.get("is_halted"):
            print(f"{Fore.RED}⛔ SYSTEM HALTED BY ADMIN. EXECUTION BLOCKED.{Style.RESET_ALL}")
            return

        if REQUIRE_ENTRY_CONFIRMATION and not is_auto:
            print(f"{Fore.CYAN}DEBUG: Checking trade_approval...{Style.RESET_ALL}")
            inst_state = state_mgr.get_state(self.symbol_base)
            trade_approval = inst_state.get("trade_approval")
            print(f"{Fore.CYAN}DEBUG: trade_approval = {trade_approval}{Style.RESET_ALL}")
            
            if inst_state.get("trade_approval") is True:
                print(f"{Fore.GREEN}DEBUG: Trade approved! Proceeding with execution...{Style.RESET_ALL}")
                state_mgr.update("trade_approval", False, symbol=self.symbol_base)
                state_mgr.update("pending_signal", None, symbol=self.symbol_base)
            else:
                print(f"{Fore.YELLOW}DEBUG: No approval yet, creating pending signal...{Style.RESET_ALL}")
                basket = self.preview_basket(trend, atm_strike, expiry_suffix)
                state_mgr.update("pending_signal", {"trend": trend, "basket": basket, "timestamp": datetime.now().strftime("%H:%M:%S")}, symbol=self.symbol_base)
                return
        
        print(f"{Fore.GREEN}DEBUG: Calling deployment function for {trend}...{Style.RESET_ALL}")
        if trend == "BULLISH": self._deploy_bullish_straight_hedge(atm_strike, expiry_suffix)
        elif trend == "BEARISH": self._deploy_bearish_straight_hedge(atm_strike, expiry_suffix)
        elif trend == "NEUTRAL": self._deploy_iron_fly(atm_strike, expiry_suffix)
        print(f"{Fore.GREEN}DEBUG: Deployment function completed{Style.RESET_ALL}")

    def _deploy_bullish_straight_hedge(self, atm_strike, expiry):
        from src.state_manager import StateManager
        state_mgr = StateManager.get_instance()
        multiplier = state_mgr.get_state().get("setup_multiplier", 1)
        
        # [NEW] Enforce Max Futures Logic
        admin_conf = state_mgr.get_state().get("admin_config", {})
        max_allowed_qty = admin_conf.get("max_futures_qty", 5)
        
        # Calculate target qty
        target_qty = max_allowed_qty * multiplier
        
        specs = SYMBOL_SPECS.get(self.symbol_base, SYMBOL_SPECS["NATGASMINI"])
        
        # CRITICAL ORDER: Risk legs → Hedge → Futures LAST
        print(f"{Fore.CYAN}📋 EXECUTING BULLISH ENTRY (Correct Order: Iron Fly → Hedge → Futures){Style.RESET_ALL}")
        self._deploy_iron_fly(atm_strike, expiry)  # 1. Iron Fly FIRST (Risk/Income legs)
        self._place_order("OPT", expiry, "BUY", target_qty, strike=atm_strike + specs["hedge_offset"], opt_type="PE", tag="HEDGE")  # 2. Hedge
        self._place_order("FUT", expiry, "BUY", target_qty)  # 3. Futures LAST

    def _deploy_bearish_straight_hedge(self, atm_strike, expiry):
        from src.state_manager import StateManager
        state_mgr = StateManager.get_instance()
        multiplier = state_mgr.get_state().get("setup_multiplier", 1)
        
        # [NEW] Enforce Max Futures Logic
        admin_conf = state_mgr.get_state().get("admin_config", {})
        max_allowed_qty = admin_conf.get("max_futures_qty", 5)
        
        # Calculate target qty
        target_qty = max_allowed_qty * multiplier
        
        specs = SYMBOL_SPECS.get(self.symbol_base, SYMBOL_SPECS["NATGASMINI"])
        
        # CRITICAL ORDER: Risk legs → Hedge → Futures LAST
        print(f"{Fore.CYAN}📋 EXECUTING BEARISH ENTRY (Correct Order: Iron Fly → Hedge → Futures){Style.RESET_ALL}")
        self._deploy_iron_fly(atm_strike, expiry)  # 1. Iron Fly FIRST (Risk/Income legs)
        self._place_order("OPT", expiry, "BUY", target_qty, strike=atm_strike - specs["hedge_offset"], opt_type="CE", tag="HEDGE")  # 2. Hedge
        self._place_order("FUT", expiry, "SELL", target_qty)  # 3. Futures LAST

    def _deploy_iron_fly(self, atm_strike, expiry):
        from src.state_manager import StateManager
        multiplier = StateManager.get_instance().get_state().get("setup_multiplier", 1)
        specs = SYMBOL_SPECS.get(self.symbol_base, SYMBOL_SPECS["NATGASMINI"])
        wd = specs["wing_dist"]
        self._place_order("OPT", expiry, "SELL", 2 * multiplier, strike=atm_strike, opt_type="CE", tag="FLY_LEG")
        self._place_order("OPT", expiry, "SELL", 2 * multiplier, strike=atm_strike, opt_type="PE", tag="FLY_LEG")
        self._place_order("OPT", expiry, "BUY", 2 * multiplier, strike=atm_strike+wd, opt_type="CE", tag="FLY_WING")
        self._place_order("OPT", expiry, "BUY", 2 * multiplier, strike=atm_strike-wd, opt_type="PE", tag="FLY_WING")

    def _place_order(self, type_, expiry, side, qty, strike=None, opt_type=None, tag=""):
        from src.config import DRY_RUN, PRODUCT_TYPE, ORDER_TYPE
        sym = construct_symbol(self.symbol_base, type_, expiry, strike, opt_type)
        print(f"   [{side}] {qty}x {sym} | {tag}")
        if not DRY_RUN: self.broker.place_order(sym, side, qty, product_type=PRODUCT_TYPE, order_type=ORDER_TYPE)
