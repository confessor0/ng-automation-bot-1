"""
Natural Gas Mini Calibration Configuration

Central configuration for the supported instrument.
Provides percentage-based hedge/wing distance calculations.
NO strategy logic changes — only parameterization.
"""

import math


# ============================================================================
# INSTRUMENT CALIBRATION TABLE
# ============================================================================

INSTRUMENT_CALIBRATION = {
    "NATGASMINI": {
        "display_name": "Natural Gas Mini",
        "exchange": "MCX",
        "lot_size": 1250,
        "strike_interval": 5,
        "expiry_day": 24,
        "hedge_pct": 0.06,
        "wings_pct": 0.12,
        "fut_lots": 5,
        "hedge_lots": 5,
        "fly_lots": 2,
        "segment": "COMMODITY",
    },
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_instrument_config(symbol: str) -> dict:
    """
    Returns full calibration config for an instrument.
    Falls back to NATGASMINI defaults if symbol not found.
    """
    config = INSTRUMENT_CALIBRATION.get(symbol)
    if config is None:
        print(f"⚠️ Unknown instrument '{symbol}', falling back to NATGASMINI defaults")
        config = INSTRUMENT_CALIBRATION["NATGASMINI"]
    return config


def get_hedge_distance(symbol: str, cmp: float) -> int:
    """
    Calculate hedge strike distance based on CMP × hedge%.
    Returns distance rounded to the nearest strike interval.
    
    Example: CMP=250, hedge_pct=0.06, interval=5
    → raw=15.0, rounded=15
    """
    config = get_instrument_config(symbol)
    interval = config["strike_interval"]
    raw_distance = cmp * config["hedge_pct"]
    
    # Round to nearest strike interval (minimum 1 interval)
    rounded = max(interval, int(round(raw_distance / interval) * interval))
    return rounded


def get_wing_distance(symbol: str, cmp: float) -> int:
    """
    Calculate wing (Iron Fly) distance based on CMP × wings%.
    Returns distance rounded to the nearest strike interval.
    """
    config = get_instrument_config(symbol)
    interval = config["strike_interval"]
    raw_distance = cmp * config["wings_pct"]
    
    # Round to nearest strike interval (minimum 1 interval)
    rounded = max(interval, int(round(raw_distance / interval) * interval))
    return rounded


def get_scenario_moves(symbol: str, cmp: float) -> list:
    """
    Generate percentage-based scenario move levels for the dashboard risk view.
    Returns absolute price moves based on: ±4%, ±8%, ±12%, ±20%
    """
    config = get_instrument_config(symbol)
    interval = config["strike_interval"]
    
    pct_levels = [0.04, 0.08, 0.12, 0.20]
    moves = []
    
    for pct in pct_levels:
        raw_move = cmp * pct
        # Round to nearest interval
        rounded_move = max(interval, int(round(raw_move / interval) * interval))
        moves.append(-rounded_move)
    
    moves.sort()  # Negative moves ascending
    moves.append(0)  # Center
    
    for pct in pct_levels:
        raw_move = cmp * pct
        rounded_move = max(interval, int(round(raw_move / interval) * interval))
        moves.append(rounded_move)
    
    return sorted(set(moves))
