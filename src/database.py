import sqlite3
import json
import os
from datetime import datetime
from src.config import TIMEZONE

class UnifiedOrderManager:
    """
    Manages persistence for the master order system using SQLite.
    Baskets and Orders are now first-class citizens with permanent records.
    """
    
    _persistent_dir = os.environ.get("TRADING_DB_DIR") or (
        "/data" if os.path.isdir("/data") else None
    )
    DB_PATH = (os.path.join(_persistent_dir, "trading_data.db") if _persistent_dir
               else ("/tmp/trading_data.db" if os.environ.get("VERCEL") else "trading_data.db"))
    
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        self._init_db()
        
    def _init_db(self):
        """Create tables if they don't exist"""
        with sqlite3.connect(self.DB_PATH) as conn:
            cursor = conn.cursor()
            
            # Baskets Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS baskets (
                    basket_id TEXT PRIMARY KEY,
                    strategy_name TEXT,
                    basket_type TEXT,
                    direction TEXT,
                    status TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    meta_data TEXT
                )
            """)
            
            # Orders Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    basket_id TEXT,
                    seq INTEGER,
                    leg_type TEXT,
                    symbol TEXT,
                    side TEXT,
                    qty INTEGER,
                    status TEXT,
                    broker_order_id TEXT,
                    fill_price REAL,
                    error TEXT,
                    timestamp TEXT,
                    FOREIGN KEY (basket_id) REFERENCES baskets (basket_id),
                    UNIQUE(basket_id, seq)
                )
            """)

            # Append-only strategy/event journal. This records every refresh
            # analysis, trend transition, and futures confirmation so the
            # dashboard can explain how a basket was produced.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS strategy_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    symbol TEXT,
                    trend TEXT,
                    signal_status TEXT,
                    futures_confirmation TEXT,
                    expiry TEXT,
                    atm_strike REAL,
                    market_price REAL,
                    occurred_at TEXT NOT NULL,
                    payload TEXT
                )
            """)
            # Add audit columns to databases created by earlier releases.
            existing = {row[1] for row in cursor.execute("PRAGMA table_info(strategy_events)").fetchall()}
            for column, definition in {
                "scan_id": "TEXT", "sequence_no": "INTEGER", "candle_at": "TEXT",
                "previous_trend": "TEXT", "raw_trend": "TEXT"
            }.items():
                if column not in existing:
                    cursor.execute(f"ALTER TABLE strategy_events ADD COLUMN {column} {definition}")

            # Historical scans are repeatable. Keep one signal row and one
            # trend-change row per candle/contract, even when the user scans
            # the same window again. Older releases could already have made
            # duplicates, so collapse those rows during startup as well.
            cursor.execute("""
                DELETE FROM strategy_events
                WHERE event_type IN ('HISTORICAL_SIGNAL', 'HISTORICAL_TREND_CHANGE')
                  AND id NOT IN (
                      SELECT MAX(id)
                      FROM strategy_events
                      WHERE event_type IN ('HISTORICAL_SIGNAL', 'HISTORICAL_TREND_CHANGE')
                      GROUP BY event_type, symbol, expiry, atm_strike, candle_at
                  )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_strategy_events_historical_key
                ON strategy_events(event_type, symbol, expiry, atm_strike, candle_at)
            """)

            # Saved, reproducible what-if calculations for any basket.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS simulations (
                    simulation_id TEXT PRIMARY KEY,
                    basket_id TEXT,
                    created_at TEXT NOT NULL,
                    entry_at TEXT,
                    exit_at TEXT,
                    quantity_multiplier REAL NOT NULL,
                    input_data TEXT NOT NULL,
                    result_data TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scan_runs (
                    scan_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    days_requested INTEGER NOT NULL,
                    from_date TEXT,
                    to_date TEXT,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS email_notification_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sent_at TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    recipients TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    alert_type TEXT,
                    metadata TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_notification_logs_sent_at ON email_notification_logs(sent_at)")
            conn.commit()

    def upsert_basket(self, basket_data: dict):
        """Insert or update a basket"""
        now = datetime.now(TIMEZONE).isoformat()
        with sqlite3.connect(self.DB_PATH) as conn:
            cursor = conn.cursor()
            
            # Extract basic fields
            b_id = basket_data["basket_id"]
            strategy = basket_data.get("strategy_name", "Benchmark")
            b_type = basket_data.get("basket_type", "ENTRY")
            direction = basket_data.get("direction", "NEUTRAL")
            status = basket_data.get("status", "DRAFT")
            created_at = basket_data.get("created_at", now)
            
            # Move everything else to meta_data JSON
            meta_keys = ["trigger_rule", "risk_snapshot", "signal_time", "signal_source", "error_message", "started_at", "completed_at"]
            meta_data = {k: basket_data.get(k) for k in meta_keys if k in basket_data}
            
            cursor.execute("""
                INSERT INTO baskets (basket_id, strategy_name, basket_type, direction, status, created_at, updated_at, meta_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(basket_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    meta_data = excluded.meta_data,
                    direction = CASE WHEN excluded.direction != 'NEUTRAL' THEN excluded.direction ELSE baskets.direction END
            """, (b_id, strategy, b_type, direction, status, created_at, now, json.dumps(meta_data)))
            
            # Sync orders if present
            if "orders" in basket_data:
                for order in basket_data["orders"]:
                    self._upsert_order(cursor, b_id, order)
                    
            conn.commit()

    def _upsert_order(self, cursor, basket_id, order):
        """Internal helper for order sync"""
        cursor.execute("""
            INSERT INTO orders (basket_id, seq, leg_type, symbol, side, qty, status, broker_order_id, fill_price, error, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(basket_id, seq) DO UPDATE SET
                status = excluded.status,
                broker_order_id = COALESCE(excluded.broker_order_id, orders.broker_order_id),
                fill_price = COALESCE(excluded.fill_price, orders.fill_price),
                error = COALESCE(excluded.error, orders.error),
                timestamp = COALESCE(excluded.timestamp, orders.timestamp),
                symbol = COALESCE(excluded.symbol, orders.symbol)
        """, (
            basket_id, 
            order["seq"], 
            order.get("type", order.get("leg_type")), 
            order.get("symbol"), 
            order["side"], 
            order["qty"], 
            order["status"], 
            order.get("order_id", order.get("broker_order_id")), 
            order.get("fill_price"), 
            order.get("error"), 
            order.get("time", order.get("timestamp"))
        ))

    def get_basket(self, basket_id: str) -> dict:
        """Fetch full basket with orders"""
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM baskets WHERE basket_id = ?", (basket_id,))
            basket_row = cursor.fetchone()
            if not basket_row:
                return None
                
            basket = dict(basket_row)
            # Parse meta_data
            if basket.get("meta_data"):
                meta = json.loads(basket["meta_data"])
                basket.update(meta)
                del basket["meta_data"]
            
            cursor.execute("SELECT * FROM orders WHERE basket_id = ? ORDER BY seq", (basket_id,))
            basket["orders"] = [dict(row) for row in cursor.fetchall()]
            
            return basket

    def list_baskets(self, limit=50, status=None, basket_type=None, strategy=None, start_date=None, end_date=None, search=None) -> list:
        """List baskets with enhanced filters"""
        query = "SELECT * FROM baskets"
        params = []
        where = []
        
        if status:
            where.append("status = ?")
            params.append(status)
        if basket_type:
            where.append("basket_type = ?")
            params.append(basket_type)
        if strategy:
            where.append("strategy_name = ?")
            params.append(strategy)
        if start_date:
            where.append("created_at >= ?")
            params.append(start_date)
        if end_date:
            where.append("created_at <= ?")
            params.append(end_date)
        if search:
            where.append("(basket_id LIKE ? OR strategy_name LIKE ?)")
            params.append(f"%{search}%")
            params.append(f"%{search}%")
            
        if where:
            query += " WHERE " + " AND ".join(where)
            
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def update_order_status(self, basket_id, seq, status, **kwargs):
        """Update a single leg status"""
        with sqlite3.connect(self.DB_PATH) as conn:
            cursor = conn.cursor()
            
            fields = ["status = ?"]
            params = [status]
            
            for k, v in kwargs.items():
                fields.append(f"{k} = ?")
                params.append(v)
                
            params.append(basket_id)
            params.append(seq)
            
            cursor.execute(f"UPDATE orders SET {', '.join(fields)} WHERE basket_id = ? AND seq = ?", params)
            conn.commit()

    def record_strategy_event(self, event_type, symbol=None, trend=None,
                              signal_status=None, futures_confirmation=None,
                              expiry=None, atm_strike=None, market_price=None,
                              payload=None, occurred_at=None, scan_id=None,
                              sequence_no=None, candle_at=None, previous_trend=None,
                              raw_trend=None, dedupe=False):
        """Persist one strategy observation.

        Historical scan events can be written repeatedly for the same candle.
        With ``dedupe=True`` they are upserted by event type, symbol, expiry,
        ATM strike, and candle timestamp. Live refresh events retain their
        append-only behavior.
        """
        occurred_at = occurred_at or datetime.now(TIMEZONE).isoformat()
        with sqlite3.connect(self.DB_PATH) as conn:
            cursor = conn.cursor()
            values = (event_type, symbol, trend, signal_status,
                      futures_confirmation, expiry, atm_strike, market_price,
                      occurred_at, json.dumps(payload or {}, default=str), scan_id,
                      sequence_no, candle_at, previous_trend, raw_trend)
            if dedupe:
                cursor.execute("""
                    SELECT id FROM strategy_events
                    WHERE event_type = ? AND symbol = ?
                      AND expiry IS ? AND atm_strike IS ? AND candle_at IS ?
                    ORDER BY id DESC LIMIT 1
                """, (event_type, symbol, expiry, atm_strike, candle_at))
                existing = cursor.fetchone()
                if existing:
                    cursor.execute("""
                        UPDATE strategy_events SET
                            trend = ?, signal_status = ?, futures_confirmation = ?,
                            market_price = ?, occurred_at = ?, payload = ?,
                            scan_id = ?, sequence_no = ?, previous_trend = ?, raw_trend = ?
                        WHERE id = ?
                    """, (trend, signal_status, futures_confirmation, market_price,
                          occurred_at, values[9], scan_id, sequence_no,
                          previous_trend, raw_trend, existing[0]))
                    conn.commit()
                    return existing[0]
            cursor.execute("""
                INSERT INTO strategy_events
                (event_type, symbol, trend, signal_status, futures_confirmation,
                 expiry, atm_strike, market_price, occurred_at, payload,
                 scan_id, sequence_no, candle_at, previous_trend, raw_trend)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, values)
            conn.commit()
            return cursor.lastrowid

    def list_strategy_events(self, limit=100, symbol=None, event_type=None,
                             start_date=None, end_date=None):
        query = "SELECT * FROM strategy_events"
        params, where = [], []
        if symbol:
            where.append("symbol = ?"); params.append(symbol)
        if event_type:
            where.append("event_type = ?"); params.append(event_type)
        if start_date:
            where.append("occurred_at >= ?"); params.append(start_date)
        if end_date:
            where.append("occurred_at <= ?"); params.append(end_date)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY occurred_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(int(limit or 100), 1000)))
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(row) for row in conn.execute(query, params).fetchall()]
        for row in rows:
            try:
                row["payload"] = json.loads(row.get("payload") or "{}")
            except (TypeError, ValueError):
                row["payload"] = {}
        return rows

    def get_strategy_event(self, event_id):
        """Return one journal event with its decoded audit payload."""
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM strategy_events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            return None
        event = dict(row)
        try:
            event["payload"] = json.loads(event.get("payload") or "{}")
        except (TypeError, ValueError):
            event["payload"] = {}
        return event

    def save_simulation(self, simulation_id, basket_id, entry_at, exit_at,
                        quantity_multiplier, input_data, result_data):
        created_at = datetime.now(TIMEZONE).isoformat()
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.execute("""
                INSERT INTO simulations
                (simulation_id, basket_id, created_at, entry_at, exit_at,
                 quantity_multiplier, input_data, result_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (simulation_id, basket_id, created_at, entry_at, exit_at,
                  quantity_multiplier, json.dumps(input_data, default=str),
                  json.dumps(result_data, default=str)))
            conn.commit()

    def list_simulations(self, limit=50, basket_id=None):
        query = "SELECT * FROM simulations"
        params = []
        if basket_id:
            query += " WHERE basket_id = ?"; params.append(basket_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit or 50), 500)))
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(row) for row in conn.execute(query, params).fetchall()]
        for row in rows:
            row["input_data"] = json.loads(row["input_data"] or "{}")
            row["result_data"] = json.loads(row["result_data"] or "{}")
        return rows

    def delete_simulation(self, simulation_id):
        """Delete one saved simulation view by its immutable ID."""
        with sqlite3.connect(self.DB_PATH) as conn:
            cursor = conn.execute("DELETE FROM simulations WHERE simulation_id = ?", (simulation_id,))
            conn.commit()
            return cursor.rowcount > 0

    def save_email_notification_log(self, provider, subject, recipients, status,
                                     error=None, alert_type=None, metadata=None):
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.execute("""
                INSERT INTO email_notification_logs
                (sent_at, provider, subject, recipients, status, error, alert_type, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (datetime.now(TIMEZONE).isoformat(), provider, subject,
                  json.dumps(recipients or []), status, error, alert_type,
                  json.dumps(metadata or {})))
            conn.commit()

    def list_email_notification_logs(self, limit=100, status=None, alert_type=None, search=None):
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            where, params = [], []
            if status:
                where.append("status = ?"); params.append(str(status).upper())
            if alert_type:
                where.append("alert_type = ?"); params.append(str(alert_type).upper())
            if search:
                where.append("(subject LIKE ? OR recipients LIKE ? OR error LIKE ?)")
                needle = f"%{search}%"; params.extend([needle, needle, needle])
            query = "SELECT * FROM email_notification_logs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
            params.append(max(1, min(int(limit or 100), 500)))
            rows = [dict(row) for row in conn.execute(query, params).fetchall()]
        for row in rows:
            for key in ("recipients", "metadata"):
                try:
                    row[key] = json.loads(row[key] or ("[]" if key == "recipients" else "{}"))
                except (TypeError, ValueError):
                    pass
        return rows

    def email_notification_health(self):
        """Return delivery counters and the latest accepted/failed attempts."""
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute("SELECT COUNT(*) AS n FROM email_notification_logs").fetchone()["n"]
            accepted = conn.execute("SELECT COUNT(*) AS n FROM email_notification_logs WHERE status = 'ACCEPTED'").fetchone()["n"]
            failed = conn.execute("SELECT COUNT(*) AS n FROM email_notification_logs WHERE status = 'FAILED'").fetchone()["n"]
            skipped = conn.execute("SELECT COUNT(*) AS n FROM email_notification_logs WHERE status = 'SKIPPED'").fetchone()["n"]
            latest = conn.execute("SELECT * FROM email_notification_logs ORDER BY id DESC LIMIT 1").fetchone()
            last_success = conn.execute("SELECT * FROM email_notification_logs WHERE status = 'ACCEPTED' ORDER BY id DESC LIMIT 1").fetchone()
            last_failure = conn.execute("SELECT * FROM email_notification_logs WHERE status = 'FAILED' ORDER BY id DESC LIMIT 1").fetchone()
        def clean(row):
            if not row: return None
            item = dict(row)
            for key in ("recipients", "metadata"):
                try: item[key] = json.loads(item[key] or ("[]" if key == "recipients" else "{}"))
                except (TypeError, ValueError): pass
            return item
        return {"total": total, "accepted": accepted, "failed": failed, "skipped": skipped,
                "latest": clean(latest), "last_success": clean(last_success), "last_failure": clean(last_failure)}

    def save_scan_run(self, scan_id, symbol, days_requested, from_date,
                      to_date, status, summary):
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.execute("""
                INSERT INTO scan_runs
                (scan_id, created_at, symbol, days_requested, from_date,
                 to_date, status, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (scan_id, datetime.now(TIMEZONE).isoformat(), symbol, days_requested,
                  from_date, to_date, status, json.dumps(summary, default=str)))
            conn.commit()

    def list_scan_runs(self, limit=25, symbol=None):
        query = "SELECT * FROM scan_runs"
        params = []
        if symbol:
            query += " WHERE symbol = ?"; params.append(symbol)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit or 25), 200)))
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(row) for row in conn.execute(query, params).fetchall()]
        for row in rows:
            row["summary"] = json.loads(row["summary"] or "{}")
        return rows
