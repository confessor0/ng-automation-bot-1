"""
Risk Manager Module
Validates capital exposure, margin utilization, and daily PnL limits.
Returns actionable risk status: SAFE, CAUTION, RISK_LOCK, HALT
"""

from colorama import Fore, Style
from src.config import CAPITAL_LIMIT, MAX_DRAWDOWN_PCT, ADJUSTMENT_TRIGGER_PCT


class RiskManager:
    """
    Centralized risk validation engine.
    Checks margin utilization, daily loss limits, and position exposure.
    """

    # Risk thresholds (percentage of capital)
    MARGIN_CAUTION_PCT = 75.0
    MARGIN_LOCK_PCT = 90.0
    MARGIN_HALT_PCT = 95.0

    def __init__(self, capital_limit=CAPITAL_LIMIT):
        self.hard_capital_limit = capital_limit
        self._state_mgr = None

    def _get_state_mgr(self):
        if not self._state_mgr:
            from src.state_manager import StateManager
            self._state_mgr = StateManager.get_instance()
        return self._state_mgr

    def validate_capital(self, available_bal):
        """
        Validates available balance against the hard capital limit.
        Returns (is_valid, status_message)
        """
        print(f"{Fore.CYAN}🔒 CAPITAL LOCK CHECK:{Style.RESET_ALL}")
        print(f"   Hard Limit: ₹{self.hard_capital_limit:,.2f} | Available: ₹{available_bal:,.2f}")

        if available_bal <= 0:
            return False, "INSUFFICIENT_CAPITAL"

        return True, "CAPITAL_OK"

    def assess_risk(self, used_margin, total_pnl, capital=None):
        """
        Comprehensive risk assessment.

        Args:
            used_margin: Current margin used
            total_pnl: Current day's MTM PnL
            capital: Total capital (defaults to CAPITAL_LIMIT)

        Returns:
            dict with:
                - status: SAFE | CAUTION | RISK_LOCK | HALT
                - margin_pct: Margin utilization percentage
                - loss_pct: Current loss as percentage of capital
                - max_loss: Maximum allowed loss amount
                - can_trade: Whether new trades are allowed
                - can_adjust: Whether adjustments are allowed
                - must_exit: Whether immediate exit is required
                - message: Human-readable status message
        """
        capital = capital or self.hard_capital_limit
        if capital <= 0:
            capital = 1.0  # Prevent division by zero

        margin_pct = (used_margin / capital) * 100 if used_margin > 0 else 0.0
        loss_pct = (total_pnl / capital) * 100 if total_pnl < 0 else 0.0
        max_loss = capital * MAX_DRAWDOWN_PCT
        adjustment_threshold = capital * ADJUSTMENT_TRIGGER_PCT

        # Determine risk status
        status = "SAFE"
        can_trade = True
        can_adjust = True
        must_exit = False
        message = "All systems nominal"

        # Check margin utilization
        if margin_pct >= self.MARGIN_HALT_PCT:
            status = "HALT"
            can_trade = False
            can_adjust = False
            must_exit = True
            message = f"🚨 CRITICAL: Margin at {margin_pct:.1f}% — EXIT IMMEDIATELY"
        elif margin_pct >= self.MARGIN_LOCK_PCT:
            status = "RISK_LOCK"
            can_trade = False
            can_adjust = True
            message = f"⚠️ Margin at {margin_pct:.1f}% — New trades blocked"
        elif margin_pct >= self.MARGIN_CAUTION_PCT:
            status = "CAUTION"
            message = f"⚡ Margin at {margin_pct:.1f}% — Monitor closely"

        # Check daily loss limits (overrides margin status if worse)
        if total_pnl < 0:
            if abs(total_pnl) >= max_loss:
                status = "HALT"
                can_trade = False
                can_adjust = False
                must_exit = True
                message = f"🚨 MAX DRAWDOWN BREACHED: ₹{total_pnl:,.2f} (limit: ₹{-max_loss:,.2f})"
            elif abs(total_pnl) >= adjustment_threshold:
                if status == "SAFE":
                    status = "CAUTION"
                message = f"⚡ Loss ₹{total_pnl:,.2f} exceeds adjustment threshold"

        result = {
            "status": status,
            "margin_pct": round(margin_pct, 2),
            "loss_pct": round(abs(loss_pct), 2),
            "max_loss": max_loss,
            "remaining_risk": max_loss - abs(total_pnl) if total_pnl < 0 else max_loss,
            "can_trade": can_trade,
            "can_adjust": can_adjust,
            "must_exit": must_exit,
            "message": message
        }

        # Print summary
        color = {
            "SAFE": Fore.GREEN,
            "CAUTION": Fore.YELLOW,
            "RISK_LOCK": Fore.RED,
            "HALT": Fore.RED
        }.get(status, Fore.WHITE)

        print(f"{color}🛡️ RISK STATUS: {status} | Margin: {margin_pct:.1f}% | PnL: ₹{total_pnl:,.2f}{Style.RESET_ALL}")

        return result

    def check_position_limits(self, current_qty, max_qty):
        """
        Validates position quantity against configured limits.

        Returns:
            (is_within_limit, available_qty)
        """
        available = max(0, max_qty - abs(current_qty))
        is_within = abs(current_qty) <= max_qty

        if not is_within:
            print(f"{Fore.RED}🚨 POSITION LIMIT EXCEEDED: {abs(current_qty)} / {max_qty}{Style.RESET_ALL}")

        return is_within, available
