"""Provider adapters for admin-configured email notifications."""
import json
import os
import time

import requests
import src.config as config


SETTINGS_FILE = os.path.join(os.getcwd(), "email_notification_config.json")


def load_provider_settings():
    """Load saved provider secrets, falling back to environment variables."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as settings_file:
            saved = json.load(settings_file)
    except (OSError, ValueError):
        saved = {}
    return {
        "provider": saved.get("provider", "mailjet"),
        "brevo_api_key": saved.get("brevo_api_key") or os.getenv("BREVO_API_KEY", ""),
        "mailjet_api_key": saved.get("mailjet_api_key") or config.MAILJET_API_KEY or "",
        "mailjet_secret_key": saved.get("mailjet_secret_key") or config.MAILJET_SECRET_KEY or "",
        "from_email": saved.get("from_email") or config.MAILJET_FROM_EMAIL or "",
        "from_name": saved.get("from_name", "NG Automation Bot"),
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
    provider = settings.get("provider", "mailjet")
    if provider == "brevo":
        configured = bool(settings.get("brevo_api_key") and settings.get("from_email"))
    else:
        configured = bool(settings.get("mailjet_api_key") and settings.get("mailjet_secret_key") and settings.get("from_email"))
    return {"configured": configured,
            "brevo_api_key_set": bool(settings.get("brevo_api_key")),
            "mailjet_api_key_set": bool(settings.get("mailjet_api_key")),
            "mailjet_secret_key_set": bool(settings.get("mailjet_secret_key"))}


def send_email_notification(subject, message, recipient, settings=None):
    """Send plain-text email through the selected Brevo or Mailjet API."""
    settings = settings or load_provider_settings()
    if not recipient or not settings.get("from_email"):
        return False
    sender = {"email": settings["from_email"], "name": settings.get("from_name") or "NG Automation Bot"}
    try:
        if settings.get("provider") == "brevo":
            if not settings.get("brevo_api_key"):
                return False
            response = requests.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": settings["brevo_api_key"], "accept": "application/json"},
                json={"sender": sender, "to": [{"email": recipient}],
                      "subject": subject, "textContent": message}, timeout=10)
        else:
            if not (settings.get("mailjet_api_key") and settings.get("mailjet_secret_key")):
                return False
            response = requests.post(
                "https://api.mailjet.com/v3.1/send",
                auth=(settings["mailjet_api_key"], settings["mailjet_secret_key"]),
                json={"Messages": [{"From": {"Email": sender["email"], "Name": sender["name"]},
                                    "To": [{"Email": recipient}], "Subject": subject,
                                    "TextPart": message}]}, timeout=10)
        response.raise_for_status()
        return True
    except Exception as exc:
        print(f"{settings.get('provider', 'mailjet').title()} notification failed: {type(exc).__name__}")
        return False


def send_mailjet_notification(subject, message, recipient=None):
    """Backward-compatible alias for the selected notification provider."""
    return send_email_notification(subject, message, recipient)


def process_due_trend_notification(state_manager):
    """Deliver a queued trend event once its configured delay has elapsed."""
    settings = state_manager.get_state().get("email_notification_config", {})
    pending = settings.get("pending_event")
    if not pending:
        return
    if not settings.get("enabled"):
        settings.pop("pending_event", None)
        state_manager.update("email_notification_config", settings)
        provider_settings = load_provider_settings()
        provider_settings["notifications"] = settings
        save_provider_settings(provider_settings)
        return
    if time.time() < float(pending.get("due_at", 0)):
        return
    if send_email_notification(pending.get("subject", "NG trend update"),
                               pending.get("message", ""), settings.get("recipient")):
        settings["last_notified_trend"] = pending.get("trend")
        settings.pop("pending_event", None)
        state_manager.update("email_notification_config", settings)
        provider_settings = load_provider_settings()
        provider_settings["notifications"] = settings
        save_provider_settings(provider_settings)
