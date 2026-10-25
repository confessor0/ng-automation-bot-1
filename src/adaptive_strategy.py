"""Decision-support builders for the finalized MCX Natural Gas strategies.

This module only builds and validates recommendations. It does not place orders.
"""

from typing import Callable, Dict, List, Optional


def _round_strike(value: float, interval: int) -> int:
    return int(round(float(value) / interval) * interval)


def _leg(action: str, instrument: str, strike: Optional[int], option_type: Optional[str], qty: int,
         symbol: Optional[str] = None, premium: Optional[float] = None) -> dict:
    return {
        "action": action,
        "instrument": instrument,
        "strike": strike,
        "option_type": option_type,
        "qty": int(qty),
        "symbol": symbol,
        "premium": round(float(premium), 4) if premium is not None else None,
    }


def _validation(legs: List[dict], *, premiums_available: bool, hedge_valid: bool = True) -> dict:
    quantities_valid = all(int(leg["qty"]) > 0 for leg in legs)
    strikes_valid = all(leg["instrument"] == "FUT" or leg["strike"] is not None for leg in legs)
    return {
        "quantities_valid": quantities_valid,
        "strikes_valid": strikes_valid,
        "premiums_available": premiums_available,
        "hedges_valid": hedge_valid,
        "execution_ready": all((quantities_valid, strikes_valid, premiums_available, hedge_valid)),
    }


def build_recommendation(
    *,
    trend: str,
    signal_status: str,
    cmp: float,
    atm_strike: int,
    expiry: str,
    option_interval: int = 5,
    quote: Optional[Callable[[str], float]] = None,
    symbol_builder: Optional[Callable[[str, str, str, int, str], str]] = None,
    futures_confirmation: str = "UNKNOWN",
    consolidation: Optional[bool] = None,
) -> dict:
    """Return a reviewable recommendation for Strategy 1, Strategy 2, or WAIT."""
    trend = str(trend or "NEUTRAL").upper()
    signal_status = signal_status or "NO_SIGNAL"
    quote = quote or (lambda _symbol: 0.0)
    symbol_builder = symbol_builder or (lambda _base, _kind, _expiry, strike, opt: f"{strike}{opt}")
    base = "NATGASMINI"

    if not cmp or not atm_strike:
        return _wait("WAIT", "A valid price and ATM strike are required before showing a basket.", cmp, atm_strike, expiry, signal_status)

    ce_symbol = symbol_builder(base, "OPT", expiry, atm_strike, "CE")
    pe_symbol = symbol_builder(base, "OPT", expiry, atm_strike, "PE")
    ce_premium, pe_premium = quote(ce_symbol), quote(pe_symbol)
    premiums_available = ce_premium > 0 and pe_premium > 0

    # Both a confirmed range and the explicit Strategy 2 selection use the
    # protected ATM straddle. The hedge strikes are derived from live premiums.
    use_consolidation = bool(consolidation) or (trend == "NEUTRAL")
    if use_consolidation:
        # Keep a reviewable neutral basket visible even when quotes are not
        # available. The fallback ±8 intervals is explicitly non-executable;
        # live premiums must replace it before validation can pass.
        combined_premium = ce_premium + pe_premium
        hedge_distance = combined_premium if premiums_available else 8 * option_interval
        put_strike = _round_strike(atm_strike - hedge_distance, option_interval)
        call_strike = _round_strike(atm_strike + hedge_distance, option_interval)
        hedge_valid = premiums_available and put_strike < atm_strike and call_strike > atm_strike
        if not hedge_valid:
            # Keep a reviewable basket visible even when current premiums are
            # too large for a valid S ± premium strike. These are placeholders
            # for review only; validation remains blocked.
            put_strike = atm_strike - (2 * option_interval)
            call_strike = atm_strike + (2 * option_interval)
        legs = [
            _leg("SELL", "OPT", atm_strike, "PE", 10, pe_symbol, pe_premium),
            _leg("SELL", "OPT", atm_strike, "CE", 10, ce_symbol, ce_premium),
            _leg("BUY", "OPT", put_strike, "PE", 10, symbol_builder(base, "OPT", expiry, put_strike, "PE"), quote(symbol_builder(base, "OPT", expiry, put_strike, "PE"))),
            _leg("BUY", "OPT", call_strike, "CE", 10, symbol_builder(base, "OPT", expiry, call_strike, "CE"), quote(symbol_builder(base, "OPT", expiry, call_strike, "CE"))),
        ]
        validation = _validation(legs, premiums_available=premiums_available, hedge_valid=hedge_valid)
        return {
            "strategy": "STRATEGY_2",
            "strategy_label": "Consolidation Markets",
            "direction": "NEUTRAL",
            "status": "READY" if validation["execution_ready"] else "CONSIDER",
            "signal_status": signal_status,
            "rationale": (("Both ATM option closes are below MA9; protected consolidation basket."
                           if futures_confirmation != "CONSOLIDATION"
                           else "Range-bound options action with a protected ATM straddle.")
                          + (" ATM premiums are missing; fallback wings are shown for review and execution remains blocked." if not premiums_available else "")
                          + (" Dynamic premium hedge strikes are invalid at current premiums; review fallback wings before execution." if not hedge_valid else "")),
            "market_context": {"current_price": cmp, "atm_strike": atm_strike, "expiry": expiry, "combined_premium": round(combined_premium, 4), "hedge_rule": "S ± (ATM CE + ATM PE), rounded to valid strike"},
            "legs": legs,
            "validation": validation,
            "trade_approved": validation["execution_ready"],
        }

    if trend == "BULLISH":
        legs = [
            _leg("BUY", "OPT", atm_strike + option_interval, "PE", 12),
            _leg("BUY", "FUT", None, None, 6),
            _leg("BUY", "OPT", atm_strike - (2 * option_interval), "CE", 6),
            _leg("SELL", "OPT", atm_strike, "CE", 4, ce_symbol, ce_premium),
            _leg("SELL", "OPT", atm_strike, "PE", 4, pe_symbol, pe_premium),
        ]
        label = "Trending Markets — Bullish"
        rationale = "Completed ATM PE close is below MA9 and ATM CE close is above MA9."
    elif trend == "BEARISH":
        legs = [
            _leg("BUY", "OPT", atm_strike - option_interval, "CE", 12),
            _leg("SELL", "FUT", None, None, 6),
            _leg("BUY", "OPT", atm_strike + (4 * option_interval), "PE", 6),
            _leg("SELL", "OPT", atm_strike, "CE", 4, ce_symbol, ce_premium),
            _leg("SELL", "OPT", atm_strike, "PE", 4, pe_symbol, pe_premium),
        ]
        label = "Trending Markets — Bearish"
        rationale = "Completed ATM CE close is below MA9 and ATM PE close is above MA9."
    else:
        return _wait("STRATEGY_1", "Signals are neutral; use the protected consolidation structure only when range conditions and premiums are confirmed.", cmp, atm_strike, expiry, signal_status)

    for leg in legs:
        if leg["instrument"] == "FUT":
            leg["symbol"] = symbol_builder(base, "FUT", expiry, 0, "")
        elif not leg.get("symbol"):
            leg["symbol"] = symbol_builder(base, "OPT", expiry, leg["strike"], leg["option_type"])
            leg["premium"] = round(float(quote(leg["symbol"])), 4)
    validation = _validation(legs, premiums_available=premiums_available)
    return {
        "strategy": "STRATEGY_1",
        "strategy_label": label,
        "direction": trend,
        "status": "READY" if validation["execution_ready"] else "WAIT",
        "signal_status": signal_status,
        "rationale": rationale,
        "market_context": {"current_price": cmp, "atm_strike": atm_strike, "expiry": expiry, "futures_confirmation": futures_confirmation},
        "legs": legs,
        "validation": validation,
        "trade_approved": validation["execution_ready"],
    }


def _wait(strategy: str, reason: str, cmp: float, atm_strike: int, expiry: str, signal_status: str) -> dict:
    return {
        "strategy": strategy,
        "strategy_label": "WAIT / NO TRADE",
        "direction": "WAIT",
        "status": "WAIT",
        "signal_status": signal_status,
        "rationale": reason,
        "market_context": {"current_price": cmp, "atm_strike": atm_strike, "expiry": expiry},
        "legs": [],
        "validation": {"execution_ready": False},
        "trade_approved": False,
    }
