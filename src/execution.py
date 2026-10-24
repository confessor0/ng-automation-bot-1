from colorama import Fore, Style
from src.state_manager import StateManager


class ExecutionEngine:
    """Risk-only broker operations used by the adjustment and exit managers."""
    def __init__(self, broker, market_data=None, symbol_base="NATGASMINI", lot_size=1250):
        self.broker = broker
        self.market = market_data
        self.symbol_base = symbol_base
        self.lot_size = lot_size
        self.state_manager = StateManager.get_instance()

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
