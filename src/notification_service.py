"""Provider adapters for admin-configured email notifications."""
import json
import os
import re
import smtplib
import time
from email.message import EmailMessage

import requests
import src.config as config


_settings_dir = os.getenv("TRADING_DB_DIR") or ("/data" if os.path.isdir("/data") else os.getcwd())
SETTINGS_FILE = os.path.join(_settings_dir, "email_notification_config.json")
LEGACY_SETTINGS_FILE = os.path.join(os.getcwd(), "email_notification_config.json")
DEFAULT_FROM_EMAIL = "mcxalerts@webprobity.com"


def normalize_recipients(value):
    """Accept a list or comma/semicolon/newline separated addresses."""
    values = value if isinstance(value, (list, tuple, set)) else re.split(r"[,;\n]+", str(value or ""))
    result, seen = [], set()
    for item in values:
        email = str(item or "").strip()
        if not email or "@" not in email or len(email) > 254:
            continue
        key = email.lower()
        if key not in seen:
            seen.add(key)
            result.append(email)
    return result


def load_provider_settings():
    """Load saved provider secrets, falling back to environment variables."""
    saved = {}
    for path in (SETTINGS_FILE, LEGACY_SETTINGS_FILE):
        if path == SETTINGS_FILE and not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as settings_file:
                saved = json.load(settings_file)
            break
        except (OSError, ValueError):
            continue
    return {
        # Brevo is the active transactional provider. Mailjet fields remain
        # readable only for backward-compatible migration of old settings.
        "provider": "brevo",
        "brevo_api_key": saved.get("brevo_api_key") or os.getenv("BREVO_API_KEY", ""),
        "brevo_transport": saved.get("brevo_transport", "api"),
        "brevo_smtp_host": saved.get("brevo_smtp_host") or os.getenv("BREVO_SMTP_HOST", "smtp-relay.brevo.com"),
        "brevo_smtp_port": int(saved.get("brevo_smtp_port") or os.getenv("BREVO_SMTP_PORT", "587")),
        "brevo_smtp_login": saved.get("brevo_smtp_login") or os.getenv("BREVO_SMTP_LOGIN", ""),
        "brevo_smtp_key": saved.get("brevo_smtp_key") or os.getenv("BREVO_SMTP_KEY", ""),
        "mailjet_api_key": saved.get("mailjet_api_key") or config.MAILJET_API_KEY or "",
        "mailjet_secret_key": saved.get("mailjet_secret_key") or config.MAILJET_SECRET_KEY or "",
        "from_email": saved.get("from_email") or config.MAILJET_FROM_EMAIL or DEFAULT_FROM_EMAIL,
        "from_name": saved.get("from_name", "NG Automation Bot"),
        "recipients": saved.get("recipients") or saved.get("notifications", {}).get("recipients", []),
        "notifications": saved.get("notifications", {}),
    }


def save_provider_settings(settings):
    """Persist provider credentials with restrictive permissions where supported."""
    temp_file = SETTINGS_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as settings_file:
        json.dump(settings, settings_file)
    os.replace(temp_file, SETTINGS_FILE)
    try:
        os.chmod(SETTINGS_FILE, 0o600)
    except OSError:
        pass


def provider_status(settings=None):
    settings = settings or load_provider_settings()
    provider = "brevo"
    if provider == "brevo":
        if settings.get("brevo_transport") == "smtp":
            configured = bool(settings.get("brevo_smtp_login") and settings.get("brevo_smtp_key") and settings.get("from_email"))
        else:
            configured = bool(settings.get("brevo_api_key") and settings.get("from_email"))
    else:
        configured = bool(settings.get("mailjet_api_key") and settings.get("mailjet_secret_key") and settings.get("from_email"))
    return {"configured": configured,
            "brevo_api_key_set": bool(settings.get("brevo_api_key")),
            "brevo_smtp_login": settings.get("brevo_smtp_login", ""),
            "brevo_smtp_key_set": bool(settings.get("brevo_smtp_key")),
            "brevo_transport": settings.get("brevo_transport", "api"),
            "mailjet_api_key_set": bool(settings.get("mailjet_api_key")),
            "mailjet_secret_key_set": bool(settings.get("mailjet_secret_key"))}


def send_email_notification(subject, message, recipient, settings=None, alert_type=None):
    """Send plain-text email through the selected Brevo or Mailjet API."""
    settings = settings or load_provider_settings()
    settings.pop("_last_error", None)
    settings.pop("_last_status", None)
    recipients = normalize_recipients(settings.get("recipients") or recipient)
    provider = settings.get("provider", "mailjet")
    status, error = "SKIPPED", None
    if not recipients or not settings.get("from_email"):
        settings["_last_error"] = "Missing recipient or sender address"
        settings["_last_status"] = "SKIPPED"
        _save_email_log(provider, subject, recipients, status, "Missing recipients or sender", alert_type)
        return False
    sender = {"email": settings["from_email"], "name": settings.get("from_name") or "NG Automation Bot"}
    try:
        if provider == "brevo":
            if settings.get("brevo_transport") == "smtp":
                if not (settings.get("brevo_smtp_login") and settings.get("brevo_smtp_key")):
                    settings["_last_error"] = "Brevo SMTP login or SMTP key is not configured"
                    settings["_last_status"] = "SKIPPED"
                    _save_email_log(provider, subject, recipients, status, settings["_last_error"], alert_type)
                    return False
                email = EmailMessage()
                email["From"] = f"{sender['name']} <{sender['email']}>"
                email["To"] = ", ".join(recipients)
                email["Subject"] = subject
                email.set_content(message)
                with smtplib.SMTP(settings.get("brevo_smtp_host", "smtp-relay.brevo.com"),
                                  int(settings.get("brevo_smtp_port", 587)), timeout=15) as smtp:
                    smtp.starttls()
                    smtp.login(settings["brevo_smtp_login"], settings["brevo_smtp_key"])
                    smtp.send_message(email)
                settings["_last_status"] = "ACCEPTED"
                _save_email_log(provider, subject, recipients, "ACCEPTED", None, alert_type,
                                {"recipient_count": len(recipients or []), "transport": "smtp"})
                return True
            if not settings.get("brevo_api_key"):
                settings["_last_error"] = "Brevo API key is not configured"
                settings["_last_status"] = "SKIPPED"
                _save_email_log(provider, subject, recipients, status, "Brevo API key is not configured", alert_type)
                return False
            response = requests.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": settings["brevo_api_key"], "accept": "application/json"},
                json={"sender": sender, "to": [{"email": email} for email in recipients],
                      "subject": subject, "textContent": message}, timeout=10)
        else:
            if not (settings.get("mailjet_api_key") and settings.get("mailjet_secret_key")):
                settings["_last_error"] = "Mailjet API key or secret is not configured"
                settings["_last_status"] = "SKIPPED"
                _save_email_log(provider, subject, recipients, status, "Mailjet credentials are not configured", alert_type)
                return False
            response = requests.post(
                "https://api.mailjet.com/v3.1/send",
                auth=(settings["mailjet_api_key"], settings["mailjet_secret_key"]),
                json={"Messages": [{"From": {"Email": sender["email"], "Name": sender["name"]},
                                    "To": [{"Email": email} for email in recipients], "Subject": subject,
                                    "TextPart": message}]}, timeout=10)
        response.raise_for_status()
        provider_result = {}
        try:
            provider_result = response.json() or {}
        except (TypeError, ValueError):
            provider_result = {}
        settings["_last_status"] = "ACCEPTED"
        _save_email_log(provider, subject, recipients, "ACCEPTED", None, alert_type,
                        {"recipient_count": len(recipients or []), "provider_response": provider_result})
        return True
    except Exception as exc:
        error = type(exc).__name__
        response = getattr(exc, "response", None)
        if response is not None:
            detail = (getattr(response, "text", "") or "").replace("\n", " ").strip()
            if detail:
                error = f"{error}: {detail[:300]}"
        settings["_last_error"] = error
        settings["_last_status"] = "FAILED"
        _save_email_log(provider, subject, recipients, "FAILED", error, alert_type)
        print(f"{provider.title()} notification failed: {error}")
        return False


def _save_email_log(provider, subject, recipients, status, error=None, alert_type=None, metadata=None):
    try:
        from src.database import UnifiedOrderManager
        UnifiedOrderManager.get_instance().save_email_notification_log(
            provider, subject, recipients, status, error=error, alert_type=alert_type,
            metadata=metadata or {"recipient_count": len(recipients or [])})
    except Exception as exc:
        print(f"Email notification log failed: {type(exc).__name__}")


def send_mailjet_notification(subject, message, recipient=None):
    """Backward-compatible alias for the selected notification provider."""
    return send_email_notification(subject, message, recipient)


def process_due_trend_notification(state_manager):
    """Deliver a queued trend event once its configured delay has elapsed."""
    settings = state_manager.get_state().get("email_notification_config", {})
    pending_key = next((key for key in ("pending_potential_event", "pending_confirmed_event", "pending_event")
                        if settings.get(key)), None)
    pending = settings.get(pending_key) if pending_key else None
    if not pending:
        return
    if not settings.get("enabled"):
        settings.pop(pending_key, None)
        state_manager.update("email_notification_config", settings)
        provider_settings = load_provider_settings()
        provider_settings["notifications"] = settings
        save_provider_settings(provider_settings)
        return
    if time.time() < float(pending.get("due_at", 0)):
        return
    provider_settings = load_provider_settings()
    provider_settings["recipients"] = settings.get("recipients") or settings.get("recipient", "")
    if send_email_notification(pending.get("subject", "NG trend update"),
                               pending.get("message", ""), provider_settings["recipients"],
                               settings=provider_settings, alert_type=pending.get("alert_type")):
        settings[f"last_notified_{str(pending.get('kind', 'confirmed')).lower()}_trend"] = pending.get("trend")
        settings.pop(pending_key, None)
        state_manager.update("email_notification_config", settings)
        provider_settings = load_provider_settings()
        provider_settings["notifications"] = settings
        save_provider_settings(provider_settings)


def process_state_alert_notifications(state_manager):
    """Email newly-created risk/expiry/system alerts once, with deduplication."""
    settings = state_manager.get_state().get("email_notification_config", {})
    if not (settings.get("enabled") and settings.get("recipient")):
        return
    alerts = list(reversed(state_manager.get_state().get("alerts", [])))
    if not alerts:
        return
    last_id = int(settings.get("last_alert_notification_id", 0) or 0)
    newest_id = max(int(a.get("id", 0) or 0) for a in alerts)
    if not last_id:
        settings["last_alert_notification_id"] = newest_id
        state_manager.update("email_notification_config", settings)
        provider_settings = load_provider_settings()
        provider_settings["notifications"] = settings
        save_provider_settings(provider_settings)
        return
    changed = False
    for alert in alerts:
        alert_id = int(alert.get("id", 0) or 0)
        if alert_id <= last_id:
            continue
        category = str(alert.get("category") or "SYSTEM").upper()
        if category in ("RISK", "EXIT", "SCALING", "ADJUSTMENT"):
            enabled = settings.get("notify_risk", True)
        elif category in ("EXPIRY", "ROLLOVER"):
            enabled = settings.get("notify_expiry", True)
        elif category in ("DATA", "DATA_QUALITY"):
            enabled = settings.get("notify_data_quality", True)
        else:
            enabled = False
        if enabled:
            priority = str(alert.get("priority") or "INFO")
            subject = f"NG {priority}: {category} alert"
            message = f"{alert.get('message', '')}\nTime (IST): {alert.get('timestamp', '-') }\nPriority: {priority}"
            provider_settings = load_provider_settings()
            provider_settings["recipients"] = settings.get("recipients") or settings.get("recipient", "")
            send_email_notification(subject, message, provider_settings["recipients"],
                                    settings=provider_settings, alert_type=category)
        last_id = max(last_id, alert_id)
        changed = True
    if changed:
        settings["last_alert_notification_id"] = last_id
        state_manager.update("email_notification_config", settings)
        provider_settings = load_provider_settings()
        provider_settings["notifications"] = settings
        save_provider_settings(provider_settings)
        return
