"""
Multi-Instrument Calibration Configuration

Central configuration for all supported instruments.
Provides percentage-based hedge/wing distance calculations.
NO strategy logic changes — only parameterization.
"""

import math


# ============================================================================
# INSTRUMENT CALIBRATION TABLE
# ============================================================================

INSTRUMENT_CALIBRATION = {
    "NATGASMINI": {
        "display_name": "Natural Gas",
        "exchange": "MCX",
        "lot_size": 250,
        "strike_interval": 5,
        "expiry_day": 24,
        "hedge_pct": 0.06,      # 6%
        "wings_pct": 0.12,      # 12%
        "fut_lots": 5,
        "hedge_lots": 5,
        "fly_lots": 2,
        "segment": "COMMODITY",
    },
    "CRUDEOILM": {
        "display_name": "Crude Oil",
        "exchange": "MCX",
        "lot_size": 10,
        "strike_interval": 100,
        "expiry_day": 18,
        "hedge_pct": 0.04,      # 4%
        "wings_pct": 0.08,      # 8%
        "fut_lots": 5,
        "hedge_lots": 5,
        "fly_lots": 2,
        "segment": "COMMODITY",
    },
    "SILVERM": {
        "display_name": "Silver",
        "exchange": "MCX",
        "lot_size": 5,
        "strike_interval": 250,
        "expiry_day": 28,
        "hedge_pct": 0.03,      # 3%
        "wings_pct": 0.06,      # 6%
        "fut_lots": 5,
        "hedge_lots": 5,
        "fly_lots": 2,
        "segment": "COMMODITY",
    },
    "GOLDM": {
        "display_name": "Gold",
        "exchange": "MCX",
        "lot_size": 10,
        "strike_interval": 100,
        "expiry_day": 4,
        "hedge_pct": 0.02,      # 2%
        "wings_pct": 0.04,      # 4%
        "fut_lots": 5,
        "hedge_lots": 5,
        "fly_lots": 2,
        "segment": "COMMODITY",
    },
    "ALUMINI": {
        "display_name": "Aluminium",
        "exchange": "MCX",
        "lot_size": 5000,
        "strike_interval": 2,
        "expiry_day": 28,
        "hedge_pct": 0.03,      # 3%
        "wings_pct": 0.06,      # 6%
        "fut_lots": 5,
        "hedge_lots": 5,
        "fly_lots": 2,
        "segment": "COMMODITY",
    },
    "NICKEL": {
        "display_name": "Nickel",
        "exchange": "MCX",
        "lot_size": 250,
        "strike_interval": 50,
        "expiry_day": 28,
        "hedge_pct": 0.05,      # 5%
        "wings_pct": 0.10,      # 10%
        "fut_lots": 5,
        "hedge_lots": 5,
        "fly_lots": 2,
        "segment": "COMMODITY",
    },
    "NIFTY": {
        "display_name": "Nifty",
        "exchange": "NSE",
        "lot_size": 25,
        "strike_interval": 50,
        "expiry_day": 0,        # Last Thursday (special handling)
        "hedge_pct": 0.015,     # 1.5%
        "wings_pct": 0.03,      # 3%
        "fut_lots": 5,
        "hedge_lots": 5,
        "fly_lots": 2,
        "segment": "INDEX",
    },
    "BANKNIFTY": {
        "display_name": "BankNifty",
        "exchange": "NSE",
        "lot_size": 15,
        "strike_interval": 100,
        "expiry_day": 0,        # Last Wednesday (special handling)
        "hedge_pct": 0.02,      # 2%
        "wings_pct": 0.04,      # 4%
        "fut_lots": 5,
        "hedge_lots": 5,
        "fly_lots": 2,
        "segment": "INDEX",
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
    Generate percentage-based scenario move levels for the simulator.
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


def get_all_instruments() -> list:
    """
    Returns list of all supported instruments with metadata for UI.
    """
    instruments = []
    for symbol, config in INSTRUMENT_CALIBRATION.items():
        instruments.append({
            "symbol": symbol,
            "display_name": config["display_name"],
            "exchange": config["exchange"],
            "lot_size": config["lot_size"],
            "strike_interval": config["strike_interval"],
            "hedge_pct": config["hedge_pct"],
            "wings_pct": config["wings_pct"],
            "segment": config["segment"],
        })
    return instruments
