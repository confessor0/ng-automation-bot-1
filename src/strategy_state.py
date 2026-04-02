"""
Strategy State Engine
Manages the strategy lifecycle state machine.
States: IDLE → READY_FOR_ENTRY → IN_POSITION → ADJUSTMENT_ALLOWED → EXIT_ALLOWED
"""

from enum import Enum
from datetime import datetime
from src.state_manager import StateManager


class StrategyState(Enum):
    """Strategy lifecycle states"""
    IDLE = "IDLE"                           # No position, waiting for trend
    READY_FOR_ENTRY = "READY_FOR_ENTRY"     # Trend confirmed, ready to create basket
    IN_POSITION = "IN_POSITION"             # Orders executed, monitoring
    ADJUSTMENT_ALLOWED = "ADJUSTMENT_ALLOWED"  # Delta breach, adjustment needed
    EXIT_ALLOWED = "EXIT_ALLOWED"           # Exit conditions met


# Valid state transitions
VALID_TRANSITIONS = {
    StrategyState.IDLE: [StrategyState.READY_FOR_ENTRY],
    StrategyState.READY_FOR_ENTRY: [StrategyState.IN_POSITION, StrategyState.IDLE],
    StrategyState.IN_POSITION: [StrategyState.ADJUSTMENT_ALLOWED, StrategyState.EXIT_ALLOWED],
    StrategyState.ADJUSTMENT_ALLOWED: [StrategyState.IN_POSITION, StrategyState.EXIT_ALLOWED],
    StrategyState.EXIT_ALLOWED: [StrategyState.IDLE],
}


class StrategyStateEngine:
    """
    Controls strategy state transitions.
    Ensures orders are only created when state allows it.
    """
    
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        self.state_mgr = StateManager.get_instance()
        self._ensure_state_exists()
    
    def _ensure_state_exists(self):
        """Initialize strategy state if not exists"""
        current = self.state_mgr.get_state().get("strategy_engine_state")
        if not current:
            self.state_mgr.update("strategy_engine_state", {
                "current_state": StrategyState.IDLE.value,
                "previous_state": None,
                "last_transition": None,
                "transition_reason": None,
                "trend_direction": None,
                "candles_in_state": 0,
                "entry_allowed": False,
                "adjustment_allowed": False,
                "exit_allowed": False
            })
    
    def get_current_state(self) -> dict:
        """Returns current strategy state with metadata"""
        state = self.state_mgr.get_state().get("strategy_engine_state", {})
        return {
            "current_state": state.get("current_state", StrategyState.IDLE.value),
            "previous_state": state.get("previous_state"),
            "last_transition": state.get("last_transition"),
            "transition_reason": state.get("transition_reason"),
            "trend_direction": state.get("trend_direction"),
            "candles_in_state": state.get("candles_in_state", 0),
            "entry_allowed": state.get("current_state") == StrategyState.READY_FOR_ENTRY.value,
            "adjustment_allowed": state.get("current_state") == StrategyState.ADJUSTMENT_ALLOWED.value,
            "exit_allowed": state.get("current_state") == StrategyState.EXIT_ALLOWED.value
        }
    
    def transition_to(self, new_state: StrategyState, reason: str, trend_direction: str = None) -> dict:
        """
        Transition to a new state if valid.
        
        Returns: {success: bool, message: str, new_state: str}
        """
        current = self.state_mgr.get_state().get("strategy_engine_state", {})
        current_state_str = current.get("current_state", StrategyState.IDLE.value)
        
        try:
            current_state = StrategyState(current_state_str)
        except ValueError:
            current_state = StrategyState.IDLE
        
        # Validate transition
        valid_next = VALID_TRANSITIONS.get(current_state, [])
        if new_state not in valid_next:
            return {
                "success": False,
                "message": f"Invalid transition: {current_state.value} → {new_state.value}",
                "current_state": current_state.value
            }
        
        # Perform transition
        new_state_data = {
            "current_state": new_state.value,
            "previous_state": current_state.value,
            "last_transition": datetime.now().isoformat(),
            "transition_reason": reason,
            "trend_direction": trend_direction or current.get("trend_direction"),
            "candles_in_state": 0,
            "entry_allowed": new_state == StrategyState.READY_FOR_ENTRY,
            "adjustment_allowed": new_state == StrategyState.ADJUSTMENT_ALLOWED,
            "exit_allowed": new_state == StrategyState.EXIT_ALLOWED
        }
        
        self.state_mgr.update("strategy_engine_state", new_state_data)
        self.state_mgr.add_log(f"🔄 State: {current_state.value} → {new_state.value} | {reason}")
        
        return {
            "success": True,
            "message": f"Transitioned to {new_state.value}",
            "new_state": new_state.value,
            "previous_state": current_state.value
        }
    
    def process_trend_signal(self, trend: str, sustained_candles: int = 0) -> dict:
        """
        Process trend signal from Trend Engine.
        This is the ENTRY POINT from Trend Engine.
        
        Args:
            trend: BULLISH, BEARISH, or NEUTRAL
            sustained_candles: Number of candles trend has been sustained
        
        Returns: Action taken (if any)
        """
        current = self.get_current_state()
        current_state = StrategyState(current["current_state"])
        
        # Rule 1: IDLE + Confirmed Trend → READY_FOR_ENTRY
        if current_state == StrategyState.IDLE:
            if trend in ["BULLISH", "BEARISH"] and sustained_candles >= 2:
                result = self.transition_to(
                    StrategyState.READY_FOR_ENTRY,
                    f"Trend {trend} sustained for {sustained_candles} candles",
                    trend_direction=trend
                )
                if result["success"]:
                    return {
                        "action": "CREATE_ORDER_INTENT",
                        "trend": trend,
                        "message": "Ready for entry - create order intent"
                    }
        
        # Rule 2: READY_FOR_ENTRY + Trend Changed → Back to IDLE
        elif current_state == StrategyState.READY_FOR_ENTRY:
            if trend == "NEUTRAL":
                self.transition_to(
                    StrategyState.IDLE,
                    "Trend became neutral before execution"
                )
                return {"action": "CANCEL_INTENT", "message": "Trend lost - cancelling intent"}
        
        # Rule 3: IN_POSITION + Exit Signal → EXIT_ALLOWED
        elif current_state == StrategyState.IN_POSITION:
            # Exit conditions would be checked here
            pass
        
        return {"action": "NONE", "message": "No state change required"}
    
    def mark_entry_executed(self) -> dict:
        """Called when entry orders are successfully executed"""
        current = self.get_current_state()
        if current["current_state"] == StrategyState.READY_FOR_ENTRY.value:
            return self.transition_to(
                StrategyState.IN_POSITION,
                "Entry orders executed successfully"
            )
        return {"success": False, "message": "Not in READY_FOR_ENTRY state"}
    
    def mark_adjustment_needed(self, reason: str) -> dict:
        """Called when adjustment is required"""
        current = self.get_current_state()
        if current["current_state"] == StrategyState.IN_POSITION.value:
            return self.transition_to(
                StrategyState.ADJUSTMENT_ALLOWED,
                reason
            )
        return {"success": False, "message": "Not in IN_POSITION state"}
    
    def mark_adjustment_done(self) -> dict:
        """Called when adjustment is completed"""
        current = self.get_current_state()
        if current["current_state"] == StrategyState.ADJUSTMENT_ALLOWED.value:
            return self.transition_to(
                StrategyState.IN_POSITION,
                "Adjustment completed"
            )
        return {"success": False, "message": "Not in ADJUSTMENT_ALLOWED state"}
    
    def mark_exit_needed(self, reason: str) -> dict:
        """Called when exit is required"""
        current = self.get_current_state()
        if current["current_state"] in [StrategyState.IN_POSITION.value, StrategyState.ADJUSTMENT_ALLOWED.value]:
            return self.transition_to(
                StrategyState.EXIT_ALLOWED,
                reason
            )
        return {"success": False, "message": "Cannot exit from current state"}
    
    def mark_exit_done(self) -> dict:
        """Called when exit is completed"""
        current = self.get_current_state()
        if current["current_state"] == StrategyState.EXIT_ALLOWED.value:
            return self.transition_to(
                StrategyState.IDLE,
                "Exit completed - returning to IDLE"
            )
        return {"success": False, "message": "Not in EXIT_ALLOWED state"}
    
    def increment_candles(self):
        """Increment candle counter in current state"""
        state = self.state_mgr.get_state().get("strategy_engine_state", {})
        state["candles_in_state"] = state.get("candles_in_state", 0) + 1
        self.state_mgr.update("strategy_engine_state", state)
