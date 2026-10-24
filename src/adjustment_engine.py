"""
Adjustment Engine
Rule-based position adjustments that trigger ONLY on candle close.
Creates new Order Intents (never bypasses the Orders flow).
"""

from datetime import datetime
from src.state_manager import StateManager
from src.strategy_state import StrategyStateEngine, StrategyState
from src.positions_analyzer import PositionsAnalyzer
from src.order_intent import OrderIntentEngine


class AdjustmentRule:
    """Defines an adjustment rule"""
    
    def __init__(self, name: str, condition_fn, action: str, priority: int = 5):
        self.name = name
        self.condition_fn = condition_fn  # Function that takes analysis and returns bool
        self.action = action  # What adjustment to make
        self.priority = priority  # Lower = higher priority
    
    def check(self, analysis: dict) -> bool:
        """Check if this rule's condition is met"""
        try:
            return self.condition_fn(analysis)
        except:
            return False


class AdjustmentEngine:
    """
    Evaluates position health and triggers adjustments when rules are met.
    
    KEY PRINCIPLE: Adjustments create ORDER INTENTS, not direct execution.
    This ensures everything goes through the Orders → Execution flow.
    """
    
    def __init__(self, broker, symbol_base: str = "NATGASMINI"):
        self.broker = broker
        self.symbol_base = symbol_base
        self.state_mgr = StateManager.get_instance()
        self.strategy_state = StrategyStateEngine.get_instance()
        self.positions_analyzer = PositionsAnalyzer(broker)
        self.intent_engine = OrderIntentEngine.get_instance(broker)
        
        # Define adjustment rules
        self.rules = self._define_rules()
    
    def _define_rules(self) -> list:
        """Define adjustment rules in priority order"""
        return [
            # Rule 1: Critical MTM Loss - Exit immediately
            AdjustmentRule(
                name="CRITICAL_MTM_EXIT",
                condition_fn=lambda a: a.get("total_mtm", 0) < -5000,
                action="EXIT_ALL",
                priority=1
            ),
            
            # Rule 2: Delta too high - Roll untested side
            AdjustmentRule(
                name="HIGH_DELTA_ROLL",
                condition_fn=lambda a: abs(a.get("net_delta", 0)) > 0.5,
                action="ROLL_UNTESTED_SIDE",
                priority=2
            ),
            
            # Rule 3: Hedge critically low - Add hedge
            AdjustmentRule(
                name="CRITICAL_HEDGE",
                condition_fn=lambda a: a.get("hedge_coverage_pct", 100) < 50,
                action="ADD_HEDGE",
                priority=3
            ),
            
            # Rule 4: Standard adjustment threshold - Reduce futures
            AdjustmentRule(
                name="STANDARD_ADJUSTMENT",
                condition_fn=lambda a: a.get("total_mtm", 0) < -2000 and a.get("futures_qty", 0) > 2,
                action="REDUCE_FUTURES",
                priority=4
            ),
            
            # Rule 5: Hedge weakening - Top up hedge
            AdjustmentRule(
                name="HEDGE_WEAKENING",
                condition_fn=lambda a: 50 <= a.get("hedge_coverage_pct", 100) < 80,
                action="TOP_UP_HEDGE",
                priority=5
            ),
        ]
    
    def check_adjustments(self) -> dict:
        """
        Check if any adjustment is needed.
        Called on CANDLE CLOSE only (not continuously).
        
        Returns: {
            needs_adjustment: bool,
            triggered_rule: str or None,
            action: str or None,
            analysis: dict
        }
        """
        # First, verify we're in the right strategy state
        current_state = self.strategy_state.get_current_state()
        
        if current_state["current_state"] not in [
            StrategyState.IN_POSITION.value,
            StrategyState.ADJUSTMENT_ALLOWED.value
        ]:
            return {
                "needs_adjustment": False,
                "message": f"Not in position (state: {current_state['current_state']})",
                "analysis": None
            }
        
        # Run position analysis
        analysis = self.positions_analyzer.analyze(self.symbol_base)
        
        if not analysis.get("has_positions"):
            return {
                "needs_adjustment": False,
                "message": "No positions to adjust",
                "analysis": analysis
            }
        
        # Check rules in priority order
        for rule in sorted(self.rules, key=lambda r: r.priority):
            if rule.check(analysis):
                return {
                    "needs_adjustment": True,
                    "triggered_rule": rule.name,
                    "action": rule.action,
                    "message": f"Adjustment triggered: {rule.name}",
                    "analysis": analysis
                }
        
        return {
            "needs_adjustment": False,
            "message": "All metrics within acceptable range",
            "analysis": analysis
        }
    
    def trigger_adjustment(self, action: str, reason: str) -> dict:
        """
        Creates an Order Intent for the adjustment.
        Does NOT execute directly - goes through standard flow.
        
        Args:
            action: ROLL_UNTESTED_SIDE, REDUCE_FUTURES, ADD_HEDGE, TOP_UP_HEDGE, EXIT_ALL
            reason: Why this adjustment was triggered
        """
        # First, transition state to ADJUSTMENT_ALLOWED
        state_result = self.strategy_state.mark_adjustment_needed(reason)
        
        if not state_result.get("success"):
            # May already be in ADJUSTMENT_ALLOWED, that's ok
            pass
        
        # Determine direction from current positions
        analysis = self.state_mgr.get_state().get("positions_analysis", {})
        futures_qty = analysis.get("futures_qty", 0)
        direction = "BULLISH" if futures_qty > 0 else "BEARISH"
        
        # Create order intent for adjustment
        intent_result = self.intent_engine.create_intent(
            direction=direction,
            basket_type="ADJUSTMENT",
            trigger_rule=f"{action}: {reason}",
            strategy_name="Benchmark Hedge"
        )
        
        if intent_result.get("success"):
            self.state_mgr.add_log(f"⚙️ Adjustment Intent Created: {action} - {reason}")
        
        return intent_result
    
    def auto_process_on_candle_close(self) -> dict:
        """
        Complete auto-processing on candle close.
        Called by the main bot loop at candle boundaries.
        
        Returns adjustment result or no-op.
        """
        check_result = self.check_adjustments()
        
        if check_result.get("needs_adjustment"):
            action = check_result.get("action")
            rule = check_result.get("triggered_rule")
            
            # For critical exit, we might want to auto-execute
            if action == "EXIT_ALL":
                self.state_mgr.add_log(f"🚨 CRITICAL: {rule} triggered EXIT_ALL")
                return self.trigger_adjustment(action, f"Rule: {rule}")
            
            # For other adjustments, create intent and wait for confirmation
            return self.trigger_adjustment(action, f"Rule: {rule}")
        
        return check_result
    
    def get_adjustment_status(self) -> dict:
        """Get current adjustment state for UI"""
        analysis = self.state_mgr.get_state().get("positions_analysis", {})
        state = self.strategy_state.get_current_state()
        
        return {
            "current_state": state.get("current_state"),
            "adjustment_allowed": state.get("adjustment_allowed"),
            "last_analysis": analysis,
            "risk_alerts": analysis.get("risk_alerts", []),
            "rules_count": len(self.rules)
        }


# Singleton accessor
def get_adjustment_engine(broker, symbol_base="NATGASMINI"):
    return AdjustmentEngine(broker, symbol_base)
