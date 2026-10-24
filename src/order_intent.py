"""
Order Intent Engine
Creates draft orders BEFORE execution.
Order Intents are planned trades that must be validated before execution.
"""

import os
import json
from datetime import datetime
from src.state_manager import StateManager
from src.order_executor import OrderExecutor
from src.database import UnifiedOrderManager


class OrderIntent:
    """
    Represents a planned trade that hasn't been executed yet.
    Status: DRAFT → VALIDATED → EXECUTING → COMPLETED/FAILED
    """
    
    def __init__(self, intent_id: str, strategy_name: str, direction: str, 
                 basket_type: str, trigger_rule: str):
        self.intent_id = intent_id
        self.strategy_name = strategy_name
        self.direction = direction  # BULLISH, BEARISH
        self.basket_type = basket_type  # ENTRY, ADJUSTMENT, EXIT
        self.trigger_rule = trigger_rule
        self.status = "DRAFT"
        self.created_at = datetime.now().isoformat()
        self.validated_at = None
        self.executed_at = None
        self.basket = []  # Will hold order details
        self.risk_validation = None
        self.error = None
    
    def to_dict(self) -> dict:
        return {
            "intent_id": self.intent_id,
            "strategy_name": self.strategy_name,
            "direction": self.direction,
            "basket_type": self.basket_type,
            "trigger_rule": self.trigger_rule,
            "status": self.status,
            "created_at": self.created_at,
            "validated_at": self.validated_at,
            "executed_at": self.executed_at,
            "basket": self.basket,
            "risk_validation": self.risk_validation,
            "error": self.error
        }


class OrderIntentEngine:
    """
    Manages order intents - the bridge between Trend/State and Execution.
    Order intents are PLANS, not ACTIONS.
    """
    
    _instance = None
    
    @classmethod
    def get_instance(cls, broker=None):
        if cls._instance is None:
            cls._instance = cls(broker)
        return cls._instance
    
    def __init__(self, broker=None):
        self.state_mgr = StateManager.get_instance()
        self.db = UnifiedOrderManager.get_instance()
        self.broker = broker
        self._current_intent = None
        self._load_current_intent()
    
    def _load_current_intent(self):
        """Load the most recent pending intent from DB"""
        pending = self.db.list_baskets(limit=1, status="DRAFT")
        if not pending:
            pending = self.db.list_baskets(limit=1, status="VALIDATED")
            
        if pending:
            self._current_intent = self.db.get_basket(pending[0]["basket_id"])
    
    def _save_intents(self):
        """Save current intent to database"""
        if self._current_intent:
            self.db.upsert_basket(self._current_intent)
    
    def create_intent(self, direction: str, basket_type: str = "ENTRY", 
                      trigger_rule: str = None, atm_strike: float = None,
                      expiry: str = None,
                      strategy_name: str = "Benchmark Hedge") -> dict:
        """
        Creates a new order intent (draft order).
        This does NOT execute - just plans the trade.
        
        Returns: {success: bool, intent: dict}
        """
        admin_config = self.state_mgr.get_state().get("admin_config", {})
        if basket_type.upper() == "ENTRY" and admin_config.get("news_risk_enabled", False):
            return {"success": False, "message": "New entries are blocked by the active News / Event Risk control."}
        # Check if there's already a pending intent
        if self._current_intent and self._current_intent.get("status") in ["DRAFT", "VALIDATED"]:
            return {
                "success": False,
                "message": "An intent already exists. Approve/cancel it first.",
                "existing_intent": self._current_intent
            }
        
        # Generate intent ID
        intent_id = f"OI_{basket_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Default trigger rule
        if trigger_rule is None:
            trigger_rule = f"Trend {direction} confirmed via strategy rules"
        
        # Create intent
        intent = OrderIntent(
            intent_id=intent_id,
            strategy_name=strategy_name,
            direction=direction,
            basket_type=basket_type,
            trigger_rule=trigger_rule
        )
        
        # If we have ATM/expiry, create the basket preview
        if atm_strike and expiry and self.broker:
            executor = OrderExecutor(self.broker)
            # Create basket but don't save to state (preview only)
            basket_preview = self._create_basket_preview(
                executor, direction, atm_strike, expiry, basket_type, strategy_name, trigger_rule
            )
            intent.basket = basket_preview
        
        self._current_intent = intent.to_dict()
        self._save_intents()
        
        # Update state manager
        self.state_mgr.update("current_order_intent", self._current_intent)
        self.state_mgr.add_log(f"📋 Order Intent Created: {intent_id} ({basket_type} {direction})")
        
        return {
            "success": True,
            "message": f"Order Intent created: {intent_id}",
            "intent": self._current_intent
        }
    
    def _create_basket_preview(self, executor, direction, atm_strike, expiry, 
                                basket_type, strategy_name, trigger_rule) -> list:
        """Creates a preview of what orders would be in the basket"""
        from src.utils import SYMBOL_SPECS
        specs = SYMBOL_SPECS.get(executor.symbol_base, SYMBOL_SPECS["NATGASMINI"])

        # Finalized adaptive strategy baskets. These are drafts only and keep
        # the existing validation/approval gate before any broker order.
        if basket_type == "ENTRY" and strategy_name.startswith("Strategy 1"):
            interval = specs.get("interval", 5)
            if direction == "BULLISH":
                legs = [("BUY", None, None, 6),
                        ("BUY", atm_strike - 2 * interval, "CE", 6),
                        ("SELL", atm_strike, "CE", 4), ("SELL", atm_strike, "PE", 4)]
            else:
                legs = [("BUY", atm_strike, "CE", 12), ("SELL", None, None, 6),
                        ("BUY", atm_strike + 4 * interval, "PE", 6),
                        ("SELL", atm_strike, "CE", 4), ("SELL", atm_strike, "PE", 4)]
            orders = []
            for seq, (side, strike, opt_type, qty) in enumerate(legs, 1):
                orders.append({"seq": seq, "type": "Adaptive Strategy 1", "leg_type": opt_type or "FUTURES",
                               "symbol": executor._construct_symbol("FUT" if strike is None else "OPT", expiry, strike, opt_type),
                               "strike": strike, "opt_type": opt_type, "side": side, "qty": qty})
            return orders

        if basket_type == "ENTRY" and strategy_name.startswith("Strategy 2"):
            interval = specs.get("interval", 5)
            fut = executor._construct_symbol("FUT", expiry)
            ce = executor._construct_symbol("OPT", expiry, atm_strike, "CE")
            pe = executor._construct_symbol("OPT", expiry, atm_strike, "PE")
            quotes = self.broker.get_quotes([ce, pe]) if self.broker else {}
            combined = float(quotes.get(ce, 0) or 0) + float(quotes.get(pe, 0) or 0)
            if combined <= 0:
                return []
            put_strike = int(round((atm_strike - combined) / interval) * interval)
            call_strike = int(round((atm_strike + combined) / interval) * interval)
            return [
                {"seq": 1, "type": "Adaptive Strategy 2", "leg_type": "ATM_PUT_SELL", "symbol": pe, "strike": atm_strike, "opt_type": "PE", "side": "SELL", "qty": 10},
                {"seq": 2, "type": "Adaptive Strategy 2", "leg_type": "ATM_CALL_SELL", "symbol": ce, "strike": atm_strike, "opt_type": "CE", "side": "SELL", "qty": 10},
                {"seq": 3, "type": "Adaptive Strategy 2", "leg_type": "HEDGE_PUT_BUY", "symbol": executor._construct_symbol("OPT", expiry, put_strike, "PE"), "strike": put_strike, "opt_type": "PE", "side": "BUY", "qty": 10},
                {"seq": 4, "type": "Adaptive Strategy 2", "leg_type": "HEDGE_CALL_BUY", "symbol": executor._construct_symbol("OPT", expiry, call_strike, "CE"), "strike": call_strike, "opt_type": "CE", "side": "BUY", "qty": 10},
            ]
        
        orders = []
        fly_qty = 2
        target_qty = 5  # Default, would come from admin config
        
        if basket_type == "ENTRY":
            # Iron Fly legs
            orders.append({
                "seq": 1, "type": "Iron Fly Wing", "leg_type": "OTM_CALL_BUY",
                "strike": atm_strike + specs.get("fly_width", 30), "opt_type": "CE",
                "side": "BUY", "qty": fly_qty
            })
            orders.append({
                "seq": 2, "type": "Iron Fly Wing", "leg_type": "OTM_PUT_BUY",
                "strike": atm_strike - specs.get("fly_width", 30), "opt_type": "PE",
                "side": "BUY", "qty": fly_qty
            })
            orders.append({
                "seq": 3, "type": "Iron Fly ATM", "leg_type": "ATM_CALL_SELL",
                "strike": atm_strike, "opt_type": "CE",
                "side": "SELL", "qty": fly_qty
            })
            orders.append({
                "seq": 4, "type": "Iron Fly ATM", "leg_type": "ATM_PUT_SELL",
                "strike": atm_strike, "opt_type": "PE",
                "side": "SELL", "qty": fly_qty
            })
            
            # ITM Hedge
            hedge_offset = specs.get("hedge_offset", 30)
            hedge_strike = atm_strike + hedge_offset if direction == "BULLISH" else atm_strike - hedge_offset
            hedge_type = "PE" if direction == "BULLISH" else "CE"
            orders.append({
                "seq": 5, "type": "ITM Hedge", "leg_type": f"ITM_{hedge_type}_BUY",
                "strike": hedge_strike, "opt_type": hedge_type,
                "side": "BUY", "qty": target_qty
            })
            
            # Futures
            fut_side = "BUY" if direction == "BULLISH" else "SELL"
            orders.append({
                "seq": 6, "type": "Futures", "leg_type": "FUTURES",
                "strike": None, "opt_type": None,
                "side": fut_side, "qty": target_qty
            })
        
        return orders
    
    def validate_intent(self) -> dict:
        """
        Validates the current intent against risk rules.
        Must pass before execution is allowed.
        """
        if not self._current_intent:
            return {"success": False, "message": "No intent to validate"}
        
        if self._current_intent.get("status") != "DRAFT":
            return {"success": False, "message": f"Intent is {self._current_intent.get('status')}, not DRAFT"}
        
        # Perform risk validation
        risk_check = self._check_risk()
        
        if risk_check["passed"]:
            self._current_intent["status"] = "VALIDATED"
            self._current_intent["validated_at"] = datetime.now().isoformat()
            self._current_intent["risk_validation"] = risk_check
            self._save_intents()
            self.state_mgr.update("current_order_intent", self._current_intent)
            self.state_mgr.add_log(f"✅ Intent Validated: {self._current_intent['intent_id']}")
            
            return {
                "success": True,
                "message": "Intent validated - ready for execution",
                "intent": self._current_intent
            }
        else:
            self._current_intent["risk_validation"] = risk_check
            self._current_intent["error"] = risk_check.get("reason")
            self._save_intents()
            self.state_mgr.update("current_order_intent", self._current_intent)
            
            return {
                "success": False,
                "message": f"Risk validation failed: {risk_check.get('reason')}",
                "risk_check": risk_check
            }
    
    def _check_risk(self) -> dict:
        """Check if trade meets risk requirements"""
        try:
            if not self.broker or not self.broker.fyers:
                return {"passed": True, "reason": "Dry run mode - auto-pass"}
            
            funds = self.broker.get_funds()
            available = funds.get("available_balance", 0)
            
            # Estimate margin needed (simplified)
            estimated_margin = 50000  # Would calculate from basket
            
            if available < estimated_margin:
                return {
                    "passed": False,
                    "reason": f"Insufficient margin. Need ₹{estimated_margin:,.0f}, have ₹{available:,.0f}",
                    "available": available,
                    "required": estimated_margin
                }
            
            return {
                "passed": True,
                "reason": "All risk checks passed",
                "available": available,
                "required": estimated_margin
            }
        except Exception as e:
            return {"passed": False, "reason": str(e)}
    
    def get_current_intent(self) -> dict:
        """Returns the current pending intent"""
        return self._current_intent
    
    def cancel_intent(self, reason: str = "User cancelled") -> dict:
        """Cancel the current intent"""
        if not self._current_intent:
            return {"success": False, "message": "No intent to cancel"}
        
        intent_id = self._current_intent.get("intent_id")
        self._current_intent["status"] = "CANCELLED"
        self._current_intent["error_message"] = reason
        
        self.db.upsert_basket(self._current_intent)
        self._current_intent = None
        self.state_mgr.update("current_order_intent", None)
        self.state_mgr.add_log(f"🚫 Intent Cancelled: {intent_id} - {reason}")
        
        return {"success": True, "message": f"Intent {intent_id} cancelled"}
    
    def mark_executing(self) -> dict:
        """Mark intent as executing (called when execution starts)"""
        if not self._current_intent:
            return {"success": False, "message": "No intent"}
        
        if self._current_intent.get("status") != "VALIDATED":
            return {"success": False, "message": "Intent not validated"}
        
        self._current_intent["status"] = "EXECUTING"
        self._current_intent["executed_at"] = datetime.now().isoformat()
        self._save_intents()
        self.state_mgr.update("current_order_intent", self._current_intent)
        
        return {"success": True, "message": "Intent marked as executing"}
    
    def mark_completed(self, basket_id: str) -> dict:
        """Mark intent as completed (called when execution succeeds)"""
        if not self._current_intent:
            return {"success": False, "message": "No intent"}
        
        self._current_intent["status"] = "COMPLETED"
        self._current_intent["basket_id"] = basket_id
        
        self.db.upsert_basket(self._current_intent)
        self._current_intent = None
        self.state_mgr.update("current_order_intent", None)
        
        return {"success": True, "message": "Intent completed and archived"}
    
    def mark_failed(self, error: str) -> dict:
        """Mark intent as failed (called when execution fails)"""
        if not self._current_intent:
            return {"success": False, "message": "No intent"}
        
        self._current_intent["status"] = "FAILED"
        self._current_intent["error_message"] = error
        
        self.db.upsert_basket(self._current_intent)
        self._current_intent = None
        self.state_mgr.update("current_order_intent", None)
        
        return {"success": True, "message": "Intent marked as failed"}
    
    @staticmethod
    def get_intent_history(limit: int = 50) -> list:
        """Get historical intents from DB"""
        db = UnifiedOrderManager.get_instance()
        return db.list_baskets(limit=limit)
