import sqlite3
import json
import os
from datetime import datetime

class UnifiedOrderManager:
    """
    Manages persistence for the master order system using SQLite.
    Baskets and Orders are now first-class citizens with permanent records.
    """
    
    DB_PATH = "/tmp/trading_data.db" if os.environ.get("VERCEL") else "trading_data.db"
    
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
            conn.commit()

    def upsert_basket(self, basket_data: dict):
        """Insert or update a basket"""
        now = datetime.now().isoformat()
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
