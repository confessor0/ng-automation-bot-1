from colorama import Fore, Style
from src.config import CAPITAL_LIMIT

class TradeManager:
    def __init__(self, broker):
        self.broker = broker
        self.state_mgr = None
        
    def _get_state_manager(self):
        # Lazy load to avoid circular import issues if any
        if not self.state_mgr:
            from src.state_manager import StateManager
            self.state_mgr = StateManager.get_instance()
        return self.state_mgr

    def monitor_positions(self, executor=None):
        """
        Fetches positions, updates Fund/Risk State, and analyzes Portfolio PnL.
        """
        self._get_state_manager()
        
        # 1. Fetch Data
        positions = self.broker.get_positions()
        funds = self.broker.get_funds() # Detailed dict
        
        # 2. Process Positions & MTM
        futures_mtm = 0.0
        options_mtm = 0.0
        total_pnl = 0.0
        
        # Mock/Estimate Margin Breakdown (Since Fyers gives aggregate)
        margin_futures = 0.0
        margin_options = 0.0
        
        print(f"\n{Fore.CYAN}📋 PORTFOLIO MONITOR{Style.RESET_ALL}")
        print("-" * 65)
        print(f"{'SYMBOL':<30} | {'QTY':<5} | {'LTP':<8} | {'PNL':<8}")
        print("-" * 65)

        for pos in positions:
            symbol = pos.get('symbol', 'Unknown')
            qty = int(pos.get('netQty', 0))
            ltp = float(pos.get('ltp', 0.0))
            pnl = float(pos.get('pl', 0.0))
            
            total_pnl += pnl
            
            # Classify MTM
            if "FUT" in symbol:
                futures_mtm += pnl
                # Estimate Margin: ~12k per lot for NG Mini ? (Rough estimate for display)
                margin_futures += abs(qty) * 12000 
            else:
                options_mtm += pnl
                # Estimate Margin: Sell ~15k, Buy ~Premium
                if qty < 0: # Sell
                    margin_options += abs(qty) * 15000
                else: # Buy
                    margin_options += abs(qty) * ltp * 1250 # Premium paid
            
            color = Fore.GREEN if pnl >= 0 else Fore.RED
            print(f"{symbol:<30} | {qty:<5} | {ltp:<8.2f} | {color}{pnl:<8.2f}{Style.RESET_ALL}")

        print("-" * 65)
        print(f"💰 TOTAL MTM: {Fore.GREEN if total_pnl >= 0 else Fore.RED}₹{total_pnl:,.2f}{Style.RESET_ALL}")
        
        # 3. Calculate Risk Metrics
        capital = funds.get("capital", 100000.0)
        # Check if 0 to avoid div by zero
        if capital == 0: capital = 1.0 
        
        used_margin = funds.get("used_margin", 0.0)
        available = funds.get("available_balance", 0.0)
        
        # Use real used_margin if available, else use our estimate
        if used_margin == 0 and (margin_futures + margin_options) > 0:
            used_margin = margin_futures + margin_options
            available = capital - used_margin

        risk_util_pct = (used_margin / capital) * 100
        
        # Determine Status
        risk_status = "SAFE"
        if risk_util_pct > 90:
            risk_status = "RISK_LOCK"
        elif risk_util_pct > 75:
            risk_status = "CAUTION"
            
        # Daily Loss Logic
        admin_conf = self.state_mgr.get_state().get("admin_config", {})
        max_daily_loss_pct = float(admin_conf.get("max_daily_loss_pct", 2.0))
        max_loss_amt = capital * (max_daily_loss_pct / 100.0)
        remaining_risk = max_loss_amt + total_pnl # If pnl is -500, remaining = 2000 + (-500) = 1500
        
        breached = total_pnl < -abs(max_loss_amt)
        
        # 4. Update State
        fund_risk_state = {
            "capital": capital,
            "used_margin": used_margin,
            "available_margin": available,
            "risk_utilisation_pct": risk_util_pct,
            "risk_status": risk_status,
            "margin_breakdown": {
                "futures": margin_futures,
                "hedge_options": margin_options, # Bundled for now
                "iron_fly": 0.0 # Placeholder until better classification
            },
            "mtm_breakdown": {
                "futures_mtm": futures_mtm,
                "hedge_mtm": options_mtm, # Bundled
                "iron_fly_mtm": 0.0,
                "net_mtm": total_pnl
            },
            "daily_risk": {
                "max_loss_allowed": max_loss_amt,
                "current_mtm": total_pnl,
                "remaining_risk": remaining_risk,
                "breached": breached
            }
        }
        
        self.state_mgr.update("fund_risk_state", fund_risk_state)
        
        # 5. Check Triggers
        # A. Critical Margin Alert
        if risk_util_pct > 95:
             print(f"{Fore.RED}🚨 CRITICAL MARGIN ALERT: {risk_util_pct:.1f}% USED. EXIT RECOMMENDED.{Style.RESET_ALL}")
             self.state_mgr.add_alert("CRITICAL", "RISK", f"Margin Usage Critical ({risk_util_pct:.1f}%). Exit Recommended.")
             # In fully auto mode, we might call executor.exit_all_positions() here
             
        # B. Daily Loss Limit (Already handled above for flag, alert here)
        if breached:
             # Already handled in calculation but good to log if fresh
             pass 
             
        # C. MTM Based Stop Loss (Legacy Config)
        self._check_risk_triggers(total_pnl, executor)
         
    def _check_risk_triggers(self, mtm, executor=None):
        """
        Checks hard stop rules & profit targets.
        Rule: Daily Max Loss 2% | Target Profit Defined in Config
        """
        from src.config import (
            TARGET_PROFIT_DAILY, MAX_DRAWDOWN_PCT, ADJUSTMENT_TRIGGER_PCT,
            ENABLE_STOP_LOSS, ENABLE_TAKE_PROFIT, CAPITAL_LIMIT
        )
        
        loss_pct = (mtm / CAPITAL_LIMIT) * 100
        
        # 1. Stop Loss Check (If Enabled)
        if ENABLE_STOP_LOSS and loss_pct <= -(MAX_DRAWDOWN_PCT * 100):
            print(f"\n{Fore.RED}🚨 CRITICAL RISK ALERT: DRAWDOWN > {MAX_DRAWDOWN_PCT*100}% (₹{mtm:.2f}){Style.RESET_ALL}")
            print(f"{Fore.RED}⚠️  STRATEGY RULE: EXIT ALL POSITIONS IMMEDIATELY.{Style.RESET_ALL}")
            if executor:
                 executor.exit_all_positions()
        
        # 2. Profit Target Check (If Enabled)
        elif ENABLE_TAKE_PROFIT and mtm >= TARGET_PROFIT_DAILY:
            print(f"\n{Fore.GREEN}🎉 TARGET ACHIEVED: Profit > ₹{TARGET_PROFIT_DAILY} (₹{mtm:.2f}){Style.RESET_ALL}")
            print(f"{Fore.GREEN}✅ LOCKING PROFITS: EXITING ALL POSITIONS.{Style.RESET_ALL}")
            if executor:
                executor.exit_all_positions()

        # 3. Adjustment Alert (1% Drawdown)
        elif loss_pct <= -(ADJUSTMENT_TRIGGER_PCT * 100):
             print(f"\n{Fore.YELLOW}⚠️  ADJUSTMENT ALERT: DRAWDOWN > {ADJUSTMENT_TRIGGER_PCT*100}% (₹{mtm:.2f}){Style.RESET_ALL}")
             print(f"{Fore.YELLOW}👉 RECOMMENDATION: Reduce Exposure. Exit 2 FUT out of 5 FUT.{Style.RESET_ALL}")
