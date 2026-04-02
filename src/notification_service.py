"""
Notification Service
Handles Telegram alerts with HTML formatting, message categories,
severity levels, and error retry logic.
"""

import requests
import time
from datetime import datetime
import src.config as config


# Message category emojis
CATEGORY_ICONS = {
    "TRADE": "📊",
    "RISK": "🛡️",
    "SYSTEM": "⚙️",
    "AUTH": "🔐",
    "ALERT": "🔔",
    "ORDER": "📋",
    "PNL": "💰",
}

# Severity prefixes
SEVERITY_PREFIX = {
    "INFO": "ℹ️",
    "WARNING": "⚠️",
    "CRITICAL": "🚨",
    "SUCCESS": "✅",
}


def send_telegram_alert(message, category="SYSTEM", severity="INFO", silent=False, max_retries=2):
    """
    Sends a formatted Telegram alert using the Bot API.
    Non-blocking: Catches exceptions to ensure trading flow isn't disrupted.

    Args:
        message: Alert text content
        category: TRADE, RISK, SYSTEM, AUTH, ALERT, ORDER, PNL
        severity: INFO, WARNING, CRITICAL, SUCCESS
        silent: If True, sends without notification sound (for low-priority)
        max_retries: Number of retry attempts on failure
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("⚠️ Telegram credentials missing. Alert skipped.")
        return False

    # Build formatted message
    icon = CATEGORY_ICONS.get(category, "📌")
    sev_icon = SEVERITY_PREFIX.get(severity, "")
    timestamp = datetime.now().strftime("%H:%M:%S")

    formatted = (
        f"{sev_icon} <b>{category}</b> {icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{message}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>🕐 {timestamp} IST</i>"
    )

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": formatted,
        "parse_mode": "HTML",
        "disable_notification": silent,
    }

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, json=payload, timeout=5)

            if response.status_code == 200:
                print(f"✅ Telegram Alert Sent [{category}/{severity}]: {message[:40]}...")
                return True
            elif response.status_code == 429:
                # Rate limited - wait and retry
                retry_after = response.json().get("parameters", {}).get("retry_after", 5)
                print(f"⚠️ Telegram rate limited. Retrying in {retry_after}s...")
                time.sleep(retry_after)
                continue
            else:
                print(f"⚠️ Telegram alert failed (attempt {attempt}): {response.text}")

        except requests.exceptions.Timeout:
            print(f"⚠️ Telegram timeout (attempt {attempt}/{max_retries})")
        except requests.exceptions.ConnectionError:
            print(f"⚠️ Telegram connection error (attempt {attempt}/{max_retries})")
        except Exception as e:
            print(f"⚠️ Telegram error (attempt {attempt}): {e}")

        # Exponential backoff between retries
        if attempt < max_retries:
            time.sleep(2 ** attempt)

    return False


def send_trade_alert(action, symbol, qty, price=None, pnl=None):
    """Convenience: Send a trade-specific alert."""
    parts = [f"<b>{action}</b>: {symbol}"]
    parts.append(f"Qty: <b>{qty}</b>")
    if price:
        parts.append(f"Price: ₹{price:,.2f}")
    if pnl is not None:
        emoji = "🟢" if pnl >= 0 else "🔴"
        parts.append(f"PnL: {emoji} ₹{pnl:,.2f}")

    return send_telegram_alert("\n".join(parts), category="TRADE", severity="INFO")


def send_risk_alert(status, message, severity="WARNING"):
    """Convenience: Send a risk-specific alert."""
    return send_telegram_alert(
        f"<b>Status: {status}</b>\n{message}",
        category="RISK",
        severity=severity,
        silent=(severity == "INFO")
    )


def send_system_alert(message, severity="INFO"):
    """Convenience: Send a system health alert."""
    return send_telegram_alert(message, category="SYSTEM", severity=severity)
