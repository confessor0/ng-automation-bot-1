"""
NG Automation Bot - Central Configuration
All system-wide constants and environment variables.
"""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


# ============================================================================
# FYERS API CREDENTIALS
# ============================================================================
FYERS_APP_ID = os.getenv("FYERS_APP_ID")
FYERS_SECRET_ID = os.getenv("FYERS_SECRET_ID")
FYERS_ID = os.getenv("FYERS_ID")
FYERS_ACCESS_TOKEN = os.getenv("FYERS_ACCESS_TOKEN")
FYERS_REDIRECT_URI = os.getenv("FYERS_REDIRECT_URI", "http://127.0.0.1:5000/callback")


# ============================================================================
# CAPITAL & RISK MANAGEMENT
# ============================================================================
CAPITAL_LIMIT = 100000.0          # ₹1,00,000 Hard Capital Limit

# Risk Thresholds
MAX_DRAWDOWN_PCT = 0.02           # 2% Max Daily Loss
TARGET_PROFIT_DAILY = 2000.0      # ₹2,000 Target Daily Profit
ADJUSTMENT_TRIGGER_PCT = 0.01    # 1% Drawdown triggers Adjustment Alert

# Strategy Toggles
ENABLE_STOP_LOSS = True           # Enable/Disable 2% Auto-Exit
ENABLE_TAKE_PROFIT = False        # Enable/Disable Target Profit Exit
REQUIRE_ENTRY_CONFIRMATION = True # Require manual approval before entry


# ============================================================================
# EXECUTION CONFIG
# ============================================================================
DRY_RUN = False                   # ⚠️ LIVE MODE: Orders sent to Exchange
ORDER_TYPE = "MARKET"             # MARKET or LIMIT
PRODUCT_TYPE = "MARGIN"           # MCX Commodity (MARGIN for overnight)
MAX_FUTURES_QTY = 3               # Maximum futures lots allowed
MAX_ADJUSTMENTS_PER_DAY = 2       # Maximum adjustments per trading day

# Adjustment Settings
ENABLE_AUTO_ADJUSTMENT = True     # Auto-execute adjustments when rule is met


# ============================================================================
# API & SERVER CONFIG
# ============================================================================
WEB_PORT = int(os.getenv("PORT", "5055"))   # Web dashboard port
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")  # Logging level

# API Optimization Mode
# True: Bot only fetches data on user refresh (saves API quota)
# False: Bot continuously polls every 15 seconds
ON_DEMAND_MODE = os.getenv("ON_DEMAND_MODE", "true").lower() == "true"


# ============================================================================
# NOTIFICATIONS
# ============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================================
# TIMEZONE & MARKET HOURS (India/Kolkata)
# Natural Gas trades 9:00 AM to 11:55 PM IST
# ============================================================================
import pytz
from datetime import datetime, time

TIMEZONE = pytz.timezone("Asia/Kolkata")
MARKET_START_TIME = time(9, 0)    # 9:00 AM IST
MARKET_END_TIME = time(23, 55)    # 11:55 PM IST


def is_market_open():
    """Checks if current time is within market hours (09:00 - 23:55 IST)."""
    now = datetime.now(TIMEZONE).time()
    return MARKET_START_TIME <= now <= MARKET_END_TIME


def get_ist_time():
    """Returns current time in IST."""
    return datetime.now(TIMEZONE)
