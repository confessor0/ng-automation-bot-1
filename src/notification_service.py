"""Mailjet notification delivery for configured trend alerts."""
import requests
import src.config as config


def send_mailjet_notification(subject, message, recipient=None):
    """Send an email through Mailjet. Returns False when unconfigured or rejected."""
    if not (config.MAILJET_API_KEY and config.MAILJET_SECRET_KEY and config.MAILJET_FROM_EMAIL and recipient):
        return False
    try:
        response = requests.post(
            "https://api.mailjet.com/v3.1/send",
            auth=(config.MAILJET_API_KEY, config.MAILJET_SECRET_KEY),
            json={"Messages": [{"From": {"Email": config.MAILJET_FROM_EMAIL},
                                "To": [{"Email": recipient}], "Subject": subject,
                                "TextPart": message}]}, timeout=10)
        response.raise_for_status()
        return True
    except Exception as exc:
        print(f"Mailjet notification failed: {type(exc).__name__}")
        return False
