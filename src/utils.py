from datetime import datetime, timedelta
import math

# Instrument Specs (MCX + NSE)
SYMBOL_SPECS = {
    "NATGASMINI": {"interval": 5, "lot": 1250, "hedge_offset": 10, "wing_dist": 20, "fly_width": 20, "expiry_day": 24, "exchange": "MCX"},
    "CRUDEOILM": {"interval": 100, "lot": 10, "hedge_offset": 100, "wing_dist": 200, "fly_width": 200, "expiry_day": 18, "exchange": "MCX"},
    "SILVERM": {"interval": 250, "lot": 5, "hedge_offset": 500, "wing_dist": 1000, "fly_width": 1000, "expiry_day": 28, "exchange": "MCX"},
    "GOLDM": {"interval": 100, "lot": 10, "hedge_offset": 200, "wing_dist": 500, "fly_width": 500, "expiry_day": 4, "exchange": "MCX"},
    "COPPER": {"interval": 5, "lot": 2500, "hedge_offset": 10, "wing_dist": 20, "fly_width": 20, "expiry_day": 28, "exchange": "MCX"},
    "ZINC": {"interval": 5, "lot": 5000, "hedge_offset": 5, "wing_dist": 10, "fly_width": 10, "expiry_day": 28, "exchange": "MCX"},
    "ALUMINI": {"interval": 2, "lot": 5000, "hedge_offset": 4, "wing_dist": 8, "fly_width": 8, "expiry_day": 28, "exchange": "MCX"},
    "LEADMINI": {"interval": 2, "lot": 5000, "hedge_offset": 4, "wing_dist": 8, "fly_width": 8, "expiry_day": 28, "exchange": "MCX"},
    "NICKEL": {"interval": 50, "lot": 250, "hedge_offset": 50, "wing_dist": 100, "fly_width": 100, "expiry_day": 28, "exchange": "MCX"},
    "NIFTY": {"interval": 50, "lot": 25, "hedge_offset": 50, "wing_dist": 100, "fly_width": 100, "expiry_day": 0, "exchange": "NSE"},
    "BANKNIFTY": {"interval": 100, "lot": 15, "hedge_offset": 100, "wing_dist": 200, "fly_width": 200, "expiry_day": 0, "exchange": "NSE"},
}

def get_month_code(date_obj):
    """
    Returns FYERS month code: JAN, FEB, MAR...
    """
    return date_obj.strftime("%b").upper()

def get_year_code(date_obj):
    """
    Returns FYERS year code: 26 (for 2026)
    """
    return date_obj.strftime("%y")

def get_next_expiry_symbol_suffix(current_date=None, symbol=None):
    """
    Determines the suffix for the current/next symbol.
    Logic: If today < Expiry Day for the symbol, use Current Month. Else Next Month.
    """
    if current_date is None:
        current_date = datetime.now()
        
    # Default to 24 (historical default for Natural gas)
    expiry_day = 24
    if symbol and symbol in SYMBOL_SPECS:
        expiry_day = SYMBOL_SPECS[symbol].get("expiry_day", 24)
    
    # Simple logic: If we are past the specific expiry_day, move to next month
    target_date = current_date
    if current_date.day > expiry_day:
        # Move to next month
        if current_date.month == 12:
            target_date = current_date.replace(year=current_date.year+1, month=1, day=1)
        else:
            target_date = current_date.replace(month=current_date.month+1, day=1)
            
    m_str = get_month_code(target_date)
    y_str = get_year_code(target_date)
    return f"{y_str}{m_str}"

def get_atm_strike(price, base="NATGASMINI"):
    """
    Returns ATM strike for given instrument.
    """
    if price is None or price == 0:
        return 0
    
    interval = SYMBOL_SPECS.get(base, {}).get("interval", 5)
    return int(round(price / interval) * interval)

def construct_symbol(base, type_, suffix, strike=None, opt_type=None):
    """
    Constructs FYERS symbol.
    MCX Futures: MCX:NATGASMINI26JANFUT
    MCX Options: MCX:NATGASMINI26JAN250CE
    NSE Futures: NSE:NIFTY26JANFUT
    NSE Options: NSE:NIFTY26JAN24000CE
    """
    # Determine exchange from SYMBOL_SPECS (default MCX for backward compat)
    exchange = SYMBOL_SPECS.get(base, {}).get("exchange", "MCX")
    
    prefix = f"{exchange}:{base}{suffix}"
    
    if type_ == "FUT":
        return f"{prefix}FUT"
    elif type_ == "OPT":
        try:
            strike_int = int(strike)
        except:
            strike_int = 0
            
        return f"{prefix}{strike_int}{opt_type}"
    return None
