"""Mobile read-only sessions, device registration, and FCM trend alerts."""
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone


def _db_path():
    directory = os.environ.get("TRADING_DB_DIR") or ("/data" if os.path.isdir("/data") else None)
    return os.path.join(directory, "mobile_app.db") if directory else "mobile_app.db"


def _connect():
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.execute("CREATE TABLE IF NOT EXISTS mobile_sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS mobile_devices (username TEXT NOT NULL, token TEXT NOT NULL, platform TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(username, token))")
    return conn


def issue_mobile_session(username):
    token = secrets.token_urlsafe(40)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    with _connect() as conn:
        conn.execute("INSERT INTO mobile_sessions(token_hash, username, expires_at) VALUES(?, ?, ?)", (token_hash, username, expiry))
    return token, expiry


def get_mobile_user(token, user_manager):
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _connect() as conn:
        row = conn.execute("SELECT username, expires_at FROM mobile_sessions WHERE token_hash = ?", (token_hash,)).fetchone()
    if not row or datetime.fromisoformat(row[1]) <= datetime.now(timezone.utc):
        if row:
            revoke_mobile_session(token)
        return None
    user = user_manager.users.get(row[0])
    return user if user and user.get("active") else None


def revoke_mobile_session(token):
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _connect() as conn:
        conn.execute("DELETE FROM mobile_sessions WHERE token_hash = ?", (token_hash,))


def register_device(username, token, platform="android"):
    with _connect() as conn:
        conn.execute("INSERT INTO mobile_devices(username, token, platform, updated_at) VALUES(?, ?, ?, ?) "
                     "ON CONFLICT(username, token) DO UPDATE SET platform=excluded.platform, updated_at=excluded.updated_at",
                     (username, token, platform, datetime.now(timezone.utc).isoformat()))


def remove_device(username, token):
    with _connect() as conn:
        conn.execute("DELETE FROM mobile_devices WHERE username = ? AND token = ?", (username, token))


def has_registered_devices():
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM mobile_devices LIMIT 1").fetchone() is not None


def send_trend_push(snapshot):
    """Send a change alert to registered Android devices when FCM is configured."""
    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if not service_account_json:
        return False
    try:
        import firebase_admin
        from firebase_admin import credentials, messaging

        try:
            firebase_app = firebase_admin.get_app()
        except ValueError:
            credential = credentials.Certificate(json.loads(service_account_json))
            firebase_app = firebase_admin.initialize_app(credential)

        with _connect() as conn:
            devices = conn.execute("SELECT token FROM mobile_devices").fetchall()
        if not devices:
            return True

        trend = str(snapshot.get("trend", "NEUTRAL"))
        strategy = str(snapshot.get("strategy_label", "WAIT / NO TRADE"))
        price = snapshot.get("price", "-")
        tokens = [row[0] for row in devices]
        invalid_tokens = []
        sent_count = 0
        for start in range(0, len(tokens), 500):
            batch = tokens[start:start + 500]
            message = messaging.MulticastMessage(
                notification=messaging.Notification(
                    title=f"Natural Gas trend: {trend}",
                    body=f"Price {price} · {strategy}"[:240],
                ),
                data={"trend": trend, "snapshot_time": str(snapshot.get("as_of", "")),
                      "strategy": strategy[:240], "price": str(price)},
                android=messaging.AndroidConfig(priority="high", ttl=3600),
                tokens=batch,
            )
            result = messaging.send_each_for_multicast(message, app=firebase_app)
            sent_count += result.success_count
            invalid_tokens.extend(batch[i] for i, response in enumerate(result.responses)
                                  if not response.success and response.exception and
                                  ("registration-token-not-registered" in str(response.exception).lower() or
                                   "invalid-registration-token" in str(response.exception).lower()))
        for token in invalid_tokens:
            with _connect() as conn:
                conn.execute("DELETE FROM mobile_devices WHERE token = ?", (token,))
        return sent_count > 0
    except Exception as exc:
        print(f"Mobile trend push failed: {type(exc).__name__}")
        return False
