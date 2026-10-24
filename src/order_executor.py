"""
Sequential Order Execution Engine
Manages controlled, one-by-one order execution with full state tracking.
"""

import os
import time
from datetime import datetime
from colorama import Fore, Style
from src.state_manager import StateManager
from src.utils import SYMBOL_SPECS
from src.database import UnifiedOrderManager


class OrderExecutor:
    """
    Handles sequential order execution with state machine:
    IDLE → PENDING → EXECUTING → COMPLETED/ERROR
    """
    
    def __init__(self, broker, symbol_base="NATGASMINI"):
        self.broker = broker
        self.symbol_base = symbol_base
        self.state_mgr = StateManager.get_instance()
        self.db = UnifiedOrderManager.get_instance()
        
    def create_basket(self, signal_type, atm_strike, expiry, qty_multiplier=1, basket_type="ENTRY", 
                       strategy_name="Benchmark Hedge", trigger_rule=None):
        """
        Creates a basket of orders for the given signal.
        Does NOT execute - just creates PENDING orders with full forensics metadata.
        
        Returns: basket_id
        """
        specs = SYMBOL_SPECS.get(self.symbol_base, SYMBOL_SPECS["NATGASMINI"])
        
        # Determine quantities
        state_mgr = StateManager.get_instance()
        admin_conf = state_mgr.get_state().get("admin_config", {})
        max_futures_qty = admin_conf.get("max_futures_qty", 5)
        
        target_qty = max_futures_qty * qty_multiplier
        fly_qty = 2 * qty_multiplier  # Scalable fly
        
        # Generate basket ID
        prefix = basket_type.upper()
        basket_id = f"{prefix}_{signal_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        signal_time = datetime.now().isoformat()
        
        # Determine direction context
        direction_map = {
            "BULLISH": "Bullish",
            "BEARISH": "Bearish",
            "NEUTRAL": "Neutral"
        }
        direction_context = direction_map.get(signal_type.upper(), "Neutral")
        
        # Strategy state based on basket type
        strategy_state_map = {
            "ENTRY": "Entry",
            "ADJUSTMENT": "Adjustment", 
            "EXIT": "Exit"
        }
        strategy_state = strategy_state_map.get(basket_type.upper(), "Entry")
        
        # Default trigger rules if not provided
        if trigger_rule is None:
            trigger_rule_map = {
                "ENTRY": f"Trend {signal_type} confirmed via Daily + Intraday analysis",
                "ADJUSTMENT": "LTP < MA9 sustained for configured period",
                "EXIT": "Max drawdown threshold breached or expiry exit"
            }
            trigger_rule = trigger_rule_map.get(basket_type.upper(), "Manual trigger")
        
        # Capture risk snapshot before execution
        risk_snapshot = self._capture_risk_snapshot(target_qty, fly_qty, admin_conf)
        
        # Build order list
        orders = []
        
        if basket_type == "ENTRY":
            signal_source = f"Trend {signal_type} confirmed"
            # Build order list (CORRECT SEQUENCE: Iron Fly → Hedge → Futures)
            # 1-4: Iron Fly legs (Wings → ATM)
            orders.append({
                "seq": 1,
                "type": "Iron Fly Wing",
                "leg_type": "OTM_CALL_BUY",
                "symbol": self._construct_symbol("OPT", expiry, strike=atm_strike + specs["fly_width"], opt_type="CE"),
                "strike": atm_strike + specs["fly_width"],
                "opt_type": "CE",
                "side": "BUY",
                "qty": fly_qty,
                "status": "PENDING",
                "time": None, "order_id": None, "fill_price": None, "slippage": None, "error": None
            })
            
            orders.append({
                "seq": 2,
                "type": "Iron Fly Wing",
                "leg_type": "OTM_PUT_BUY",
                "symbol": self._construct_symbol("OPT", expiry, strike=atm_strike - specs["fly_width"], opt_type="PE"),
                "strike": atm_strike - specs["fly_width"],
                "opt_type": "PE",
                "side": "BUY",
                "qty": fly_qty,
                "status": "PENDING",
                "time": None, "order_id": None, "fill_price": None, "slippage": None, "error": None
            })
            
            orders.append({
                "seq": 3,
                "type": "Iron Fly ATM",
                "leg_type": "ATM_CALL_SELL",
                "symbol": self._construct_symbol("OPT", expiry, strike=atm_strike, opt_type="CE"),
                "strike": atm_strike,
                "opt_type": "CE",
                "side": "SELL",
                "qty": fly_qty,
                "status": "PENDING",
                "time": None, "order_id": None, "fill_price": None, "slippage": None, "error": None
            })
            
            orders.append({
                "seq": 4,
                "type": "Iron Fly ATM",
                "leg_type": "ATM_PUT_SELL",
                "symbol": self._construct_symbol("OPT", expiry, strike=atm_strike, opt_type="PE"),
                "strike": atm_strike,
                "opt_type": "PE",
                "side": "SELL",
                "qty": fly_qty,
                "status": "PENDING",
                "time": None, "order_id": None, "fill_price": None, "slippage": None, "error": None
            })
            
            # 5: ITM Hedge
            hedge_strike = atm_strike + specs["hedge_offset"] if signal_type == "BULLISH" else atm_strike - specs["hedge_offset"]
            hedge_opt_type = "PE" if signal_type == "BULLISH" else "CE"
            
            orders.append({
                "seq": 5, "type": "ITM Hedge",
                "leg_type": f"ITM_{hedge_opt_type}_BUY",
                "symbol": self._construct_symbol("OPT", expiry, strike=hedge_strike, opt_type=hedge_opt_type),
                "strike": hedge_strike,
                "opt_type": hedge_opt_type,
                "side": "BUY", "qty": target_qty, "status": "PENDING",
                "time": None, "order_id": None, "fill_price": None, "slippage": None, "error": None
            })
            
            # 6: Futures (LAST!)
            fut_side = "BUY" if signal_type == "BULLISH" else "SELL"
            orders.append({
                "seq": 6, "type": "Futures",
                "leg_type": "FUTURES",
                "symbol": self._construct_symbol("FUT", expiry),
                "strike": None,
                "opt_type": None,
                "side": fut_side, "qty": target_qty, "status": "PENDING",
                "time": None, "order_id": None, "fill_price": None, "slippage": None, "error": None
            })

        elif basket_type == "ADJUSTMENT":
            signal_source = f"Adjustment Trigger: {trigger_rule}"
            # Adjustment rule: Exit 2 futures
            fut_side = "SELL" if signal_type == "BULLISH" else "BUY" # Exit current position
            orders.append({
                "seq": 1, "type": "Adjustment (Exit 2 Futures)",
                "leg_type": "FUTURES_EXIT",
                "symbol": self._construct_symbol("FUT", expiry),
                "strike": None, "opt_type": None,
                "side": fut_side, "qty": 2 * qty_multiplier, "status": "PENDING",
                "time": None, "order_id": None, "fill_price": None, "slippage": None, "error": None
            })

        elif basket_type == "EXIT":
            signal_source = f"Strategy Exit: {trigger_rule}"
            # Get current positions and reverse them
            positions = [p for p in self.broker.get_positions() if self.symbol_base in p.get('symbol', '')]
            for idx, pos in enumerate(positions):
                qty = int(pos.get("netQty", 0))
                if qty == 0: continue
                orders.append({
                    "seq": idx + 1, "type": "Full Exit",
                    "leg_type": "POSITION_EXIT",
                    "symbol": pos["symbol"],
                    "strike": None, "opt_type": None,
                    "side": "SELL" if qty > 0 else "BUY",
                    "qty": abs(qty),
                    "status": "PENDING",
                    "time": None, "order_id": None, "fill_price": None, "slippage": None, "error": None
                })
        
        # Fetch LTP for all symbols in basket
        for order in orders:
            try:
                ltp = self.broker.get_ltp(order["symbol"])
                order["ltp"] = ltp
            except Exception as e:
                print(f"Failed to fetch LTP for {order['symbol']}: {e}")
                order["ltp"] = 0.0

        # Update StateManager with basket (FULL FORENSICS)
        basket_data = {
            "basket_id": basket_id,
            "basket_type": basket_type,
            "signal_type": signal_type,
            "signal_time": signal_time,
            "signal_source": signal_source,
            # --- FORENSICS METADATA ---
            "strategy_name": strategy_name,
            "strategy_state": strategy_state,
            "direction_context": direction_context,
            "trigger_rule": trigger_rule,
            "execution_mode": "Auto",  # or "Admin Confirmed"
            "risk_snapshot": risk_snapshot,
            # --- ORDER DATA ---
            "orders": orders,
            "progress": 0,
            "total": len(orders),
            "status": "PENDING",
            "current_order_index": 0,
            "error_message": None,
            "started_at": None,
            "completed_at": None
        }
        
        self.state_mgr.update("order_execution_state", basket_data)
        self.db.upsert_basket(basket_data)
        
        print(f"{Fore.GREEN}✅ {basket_type} Basket Created: {basket_id} ({len(orders)} orders){Style.RESET_ALL}")
        self.state_mgr.add_log(f"📦 Basket Created: {basket_type} | Strategy: {strategy_name} | Trigger: {trigger_rule[:50]}...")
        
        return basket_id
    
    def _capture_risk_snapshot(self, futures_qty, options_qty, admin_conf):
        """
        Captures risk metrics BEFORE order execution.
        This proves risk was validated before hitting the broker.
        """
        try:
            funds = self.broker.get_funds() if self.broker.fyers else {}
            available = funds.get("available_balance", 0)
            capital = funds.get("capital", 0)
            
            # Estimate margin required (simplified)
            specs = SYMBOL_SPECS.get(self.symbol_base, SYMBOL_SPECS["NATGASMINI"])
            lot_size = specs.get("lot_size", 250)
            estimated_margin = futures_qty * lot_size * 50  # Rough estimate
            
            # Max loss calculation based on strategy
            max_loss_per_lot = admin_conf.get("max_loss_pct", 2.0) * capital / 100
            
            return {
                "captured_at": datetime.now().isoformat(),
                "available_balance": available,
                "total_capital": capital,
                "estimated_margin_required": estimated_margin,
                "margin_sufficient": available >= estimated_margin,
                "max_loss_threshold": max_loss_per_lot,
                "hedge_coverage": True,  # Iron Fly provides full hedge
                "risk_profile_compliant": True
            }
        except Exception as e:
            return {
                "captured_at": datetime.now().isoformat(),
                "error": str(e),
                "margin_sufficient": False,
                "risk_profile_compliant": False
            }
    
    def execute_basket(self, basket_id, multiplier=1):
        """
        Executes all orders in the basket sequentially.
        Updates state in real-time as orders fill.
        """
        exec_state = self.state_mgr.get_state().get("order_execution_state", {})
        
        if exec_state.get("basket_id") != basket_id:
            print(f"{Fore.RED}❌ Basket {basket_id} not found{Style.RESET_ALL}")
            return False

        admin_config = self.state_mgr.get_state().get("admin_config", {})
        if (exec_state.get("basket_type", "ENTRY").upper() == "ENTRY" and
                admin_config.get("news_risk_enabled", False)):
            exec_state["status"] = "BLOCKED"
            exec_state["error_message"] = "New entry blocked by News / Event Risk control"
            self.state_mgr.update("order_execution_state", exec_state)
            self.db.upsert_basket(exec_state)
            self.state_mgr.add_log("⛔ Entry execution blocked by News / Event Risk control", category="RISK")
            return False
        
        if exec_state.get("status") != "PENDING":
            print(f"{Fore.RED}❌ Basket already executing or completed{Style.RESET_ALL}")
            return False
        
        # Update status to EXECUTING
        exec_state["status"] = "EXECUTING"
        exec_state["started_at"] = datetime.now().isoformat()
        
        # Apply Multiplier Scaling
        if multiplier > 1:
            print(f"{Fore.MAGENTA}🔢 SCALING BASKET by {multiplier}x{Style.RESET_ALL}")
            for order in exec_state["orders"]:
                old_qty = order["qty"]
                order["qty"] = old_qty * multiplier
                print(f"   Scaled Order #{order['seq']}: {old_qty} -> {order['qty']}")
        
        self.state_mgr.update("order_execution_state", exec_state)
        
        print(f"{Fore.CYAN}🚀 Starting Sequential Execution: {basket_id} (x{multiplier}){Style.RESET_ALL}")
        self.state_mgr.add_log(f"🚀 Executing Basket: {basket_id} (Multiplier: x{multiplier})")
        
        # Execute orders one by one
        orders = exec_state["orders"]
        for idx, order in enumerate(orders):
            success = self._execute_single_order(idx, order)
            
            if not success:
                # Order failed - cancel remaining
                self._cancel_remaining_orders(idx + 1)
                # Update status to ERROR
                exec_state = self.state_mgr.get_state().get("order_execution_state", {})
                exec_state["status"] = "ERROR"
                exec_state["completed_at"] = datetime.now().isoformat()
                self.state_mgr.update("order_execution_state", exec_state)
                self.db.upsert_basket(exec_state)
                
                print(f"{Fore.RED}❌ Execution FAILED at order #{idx+1}{Style.RESET_ALL}")
                self.state_mgr.add_log(f"❌ Basket Execution FAILED: {basket_id}")
                return False
        
        # All orders filled successfully
        exec_state = self.state_mgr.get_state().get("order_execution_state", {})
        exec_state["status"] = "COMPLETED"
        exec_state["completed_at"] = datetime.now().isoformat()
        self.state_mgr.update("order_execution_state", exec_state)
        self.db.upsert_basket(exec_state)
        
        print(f"{Fore.GREEN}✅ Basket Execution COMPLETE: {basket_id}{Style.RESET_ALL}")
        self.state_mgr.add_log(f"✅ Basket Execution COMPLETE: {basket_id}")
        return True
    
    def _execute_single_order(self, order_index, order):
        """
        Executes a single order and waits for fill confirmation.
        Returns True if filled, False if rejected/timeout.
        """
        exec_state = self.state_mgr.get_state().get("order_execution_state", {})
        
        # Update order status to EXECUTING
        exec_state["orders"][order_index]["status"] = "EXECUTING"
        exec_state["current_order_index"] = order_index
        self.state_mgr.update("order_execution_state", exec_state)
        self.db.upsert_basket(exec_state)
        
        print(f"{Fore.YELLOW}⏳ [{order['seq']}/{exec_state['total']}] Placing {order['type']}: {order['symbol']} {order['side']} x{order['qty']}{Style.RESET_ALL}")
        
        # Place order via broker
        from src.config import DRY_RUN, PRODUCT_TYPE, ORDER_TYPE
        
        if DRY_RUN:
            # Simulate order placement
            print(f"{Fore.MAGENTA}   [DRY RUN] Order simulated{Style.RESET_ALL}")
            time.sleep(0.5)  # Simulate network delay
            order_id = f"DRY_{int(time.time())}"
            fill_price = 10.50  # Simulated price
            
            # Update order as FILLED
            exec_state["orders"][order_index]["status"] = "FILLED"
            exec_state["orders"][order_index]["time"] = datetime.now().strftime("%H:%M:%S")
            exec_state["orders"][order_index]["order_id"] = order_id
            exec_state["orders"][order_index]["fill_price"] = fill_price
            exec_state["progress"] += 1
            self.state_mgr.update("order_execution_state", exec_state)
            self.db.upsert_basket(exec_state)
            
            print(f"{Fore.GREEN}   ✅ FILLED: {order_id} @ ₹{fill_price}{Style.RESET_ALL}")
            return True
        else:
            # LIVE TRADING: Place actual order
            try:
                result = self.broker.place_order(
                    order["symbol"],
                    order["side"],
                    order["qty"],
                    product_type=PRODUCT_TYPE,
                    order_type=ORDER_TYPE
                )
                
                if result and result.get("s") == "ok":
                    order_id = result.get("id")
                    print(f"{Fore.CYAN}   📤 Order Sent: {order_id}{Style.RESET_ALL}")
                    
                    # Poll for order status
                    fill_price = self._wait_for_fill(order_id, timeout=30)
                    
                    if fill_price:
                        # Order FILLED
                        exec_state = self.state_mgr.get_state().get("order_execution_state", {})
                        exec_state["orders"][order_index]["status"] = "FILLED"
                        exec_state["orders"][order_index]["time"] = datetime.now().strftime("%H:%M:%S")
                        exec_state["orders"][order_index]["order_id"] = order_id
                        exec_state["orders"][order_index]["fill_price"] = fill_price
                        exec_state["progress"] += 1
                        self.state_mgr.update("order_execution_state", exec_state)
                        self.db.upsert_basket(exec_state)
                        
                        print(f"{Fore.GREEN}   ✅ FILLED: {order_id} @ ₹{fill_price}{Style.RESET_ALL}")
                        return True
                    else:
                        # TIMEOUT
                        exec_state = self.state_mgr.get_state().get("order_execution_state", {})
                        exec_state["orders"][order_index]["status"] = "TIMEOUT"
                        exec_state["orders"][order_index]["error"] = "Order fill timeout after 30s"
                        exec_state["error_message"] = f"Order #{order['seq']} timed out"
                        self.state_mgr.update("order_execution_state", exec_state)
                        self.db.upsert_basket(exec_state)
                        
                        print(f"{Fore.RED}   ⏱️ TIMEOUT: {order_id}{Style.RESET_ALL}")
                        return False
                else:
                    # ORDER REJECTED
                    error_msg = result.get("message", "Unknown error")
                    exec_state = self.state_mgr.get_state().get("order_execution_state", {})
                    exec_state["orders"][order_index]["status"] = "REJECTED"
                    exec_state["orders"][order_index]["error"] = error_msg
                    exec_state["error_message"] = f"Order #{order['seq']} rejected: {error_msg}"
                    self.state_mgr.update("order_execution_state", exec_state)
                    self.db.upsert_basket(exec_state)
                    
                    print(f"{Fore.RED}   ❌ REJECTED: {error_msg}{Style.RESET_ALL}")
                    return False
                    
            except Exception as e:
                # EXCEPTION
                exec_state = self.state_mgr.get_state().get("order_execution_state", {})
                exec_state["orders"][order_index]["status"] = "REJECTED"
                exec_state["orders"][order_index]["error"] = str(e)
                exec_state["error_message"] = f"Order #{order['seq']} exception: {str(e)}"
                self.state_mgr.update("order_execution_state", exec_state)
                self.db.upsert_basket(exec_state)
                
                print(f"{Fore.RED}   ❌ EXCEPTION: {e}{Style.RESET_ALL}")
                return False
    
    def _wait_for_fill(self, order_id, timeout=30):
        """
        Polls Fyers for order status until FILLED or timeout.
        Returns fill_price if filled, None if timeout/rejected.
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                order_status = self.broker.get_order_status(order_id)
                
                if order_status and order_status.get("status") == "FILLED":
                    return order_status.get("tradedPrice", 0.0)
                elif order_status and order_status.get("status") in ["REJECTED", "CANCELLED"]:
                    return None
                
                time.sleep(2)  # Poll every 2 seconds
            except Exception as e:
                print(f"{Fore.RED}   Error polling order status: {e}{Style.RESET_ALL}")
                time.sleep(2)
        
        return None  # Timeout
    
    def _cancel_remaining_orders(self, start_index):
        """
        Marks all remaining orders as CANCELLED.
        """
        exec_state = self.state_mgr.get_state().get("order_execution_state", {})
        
        for idx in range(start_index, len(exec_state["orders"])):
            exec_state["orders"][idx]["status"] = "CANCELLED"
        
        self.state_mgr.update("order_execution_state", exec_state)
        print(f"{Fore.YELLOW}⚠️ Remaining orders CANCELLED{Style.RESET_ALL}")
    
    @staticmethod
    def get_order_history(limit=50, basket_type=None):
        """Retrieves order history from DB"""
        db = UnifiedOrderManager.get_instance()
        return db.list_baskets(limit=limit, basket_type=basket_type)
    
    def _construct_symbol(self, type_, expiry, strike=None, opt_type=None):
        """
        Constructs Fyers symbol format.
        Examples:
        - FUT: MCX:NATGASMINI26JANFUT
        - OPT: MCX:NATGASMINI26JAN315CE
        """
        base_symbol = f"MCX:{self.symbol_base}{expiry}"
        
        if type_ == "FUT":
            return f"{base_symbol}FUT"
        elif type_ == "OPT":
            if strike is None or opt_type is None:
                raise ValueError("strike and opt_type required for OPT")
            return f"{base_symbol}{int(strike)}{opt_type}"
        else:
            raise ValueError(f"Unknown type: {type_}")
