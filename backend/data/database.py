import sqlite3
import json
from datetime import datetime, date
from typing import List, Dict, Optional
from config import config

class Database:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.DATABASE_PATH
        self.init_db()

    def get_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        """Initialize database tables"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Ticks table - store market data
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                ltp REAL NOT NULL,
                bid_qty INTEGER NOT NULL,
                ask_qty INTEGER NOT NULL,
                volume INTEGER NOT NULL,
                bid_price REAL,
                ask_price REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migrate existing DBs that predate bid_price/ask_price columns
        for col in ["bid_price REAL", "ask_price REAL"]:
            try:
                cursor.execute(f"ALTER TABLE ticks ADD COLUMN {col}")
            except Exception:
                pass  # already exists
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts
            ON ticks (symbol, timestamp DESC)
        """)

        # Trades table - store all trades
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                entry_time TIMESTAMP NOT NULL,
                entry_qty INTEGER NOT NULL,
                exit_price REAL,
                exit_time TIMESTAMP,
                exit_reason TEXT,
                stop_loss_price REAL NOT NULL,
                pnl REAL,
                mode TEXT NOT NULL DEFAULT 'paper',
                -- Signal metadata at entry (for ML training)
                entry_ratio REAL,
                entry_ofi REAL,
                entry_signal_confidence REAL,
                entry_norm_imbalance REAL,
                entry_minutes_since_open INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Add signal metadata columns to existing DBs that predate this schema
        for col, typedef in [
            ('entry_ratio', 'REAL'),
            ('entry_ofi', 'REAL'),
            ('entry_signal_confidence', 'REAL'),
            ('entry_norm_imbalance', 'REAL'),
            ('entry_minutes_since_open', 'INTEGER'),
            ('is_flip', 'INTEGER DEFAULT 0'),   # 1 = this trade was opened as a momentum flip
        ]:
            try:
                cursor.execute(f"ALTER TABLE trades ADD COLUMN {col} {typedef}")
            except Exception:
                pass  # column already exists

        # Settings table - persist app state across restarts
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Daily summary table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date DATE NOT NULL UNIQUE,
                total_pnl REAL DEFAULT 0,
                trades_count INTEGER DEFAULT 0,
                win_count INTEGER DEFAULT 0,
                loss_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()
        print(f"✓ Database initialized at {self.db_path}")

    def insert_tick(self, symbol: str, timestamp: int, ltp: float, bid_qty: int, ask_qty: int,
                    volume: int, bid_price: float = None, ask_price: float = None):
        """Insert a tick into the database"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO ticks (symbol, timestamp, ltp, bid_qty, ask_qty, volume, bid_price, ask_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (symbol, timestamp, ltp, bid_qty, ask_qty, volume, bid_price, ask_price))
        conn.commit()
        conn.close()

    def insert_trade(self, symbol: str, direction: str, entry_price: float, entry_qty: int,
                     stop_loss_price: float, signal_meta: dict = None, is_flip: bool = False):
        """Insert a new trade with optional signal metadata for ML training"""
        conn = self.get_connection()
        cursor = conn.cursor()
        meta = signal_meta or {}
        cursor.execute("""
            INSERT INTO trades (
                symbol, direction, entry_price, entry_time, entry_qty, stop_loss_price, mode,
                entry_ratio, entry_ofi, entry_signal_confidence, entry_norm_imbalance, entry_minutes_since_open,
                is_flip
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, direction, entry_price, datetime.now(), entry_qty, stop_loss_price,
            'paper' if config.PAPER_TRADING else 'live',
            meta.get('ratio'), meta.get('ofi'), meta.get('confidence'),
            meta.get('norm_imbalance'), meta.get('minutes_since_open'),
            1 if is_flip else 0
        ))
        conn.commit()
        trade_id = cursor.lastrowid
        conn.close()
        return trade_id

    def update_trade_exit(self, trade_id: int, exit_price: float, exit_reason: str):
        """Update a trade with exit info"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Get entry price to calculate P&L
        cursor.execute("SELECT entry_price, entry_qty, direction FROM trades WHERE id = ?", (trade_id,))
        row = cursor.fetchone()

        if row:
            entry_price = row[0]
            entry_qty = row[1]
            direction = row[2]

            # Calculate P&L
            if direction == "BUY":
                pnl = (exit_price - entry_price) * entry_qty
            else:  # SELL
                pnl = (entry_price - exit_price) * entry_qty

            cursor.execute("""
                UPDATE trades
                SET exit_price = ?, exit_time = ?, exit_reason = ?, pnl = ?
                WHERE id = ?
            """, (exit_price, datetime.now(), exit_reason, pnl, trade_id))

            conn.commit()

        conn.close()

    def get_active_trades(self) -> List[Dict]:
        """Get all active (not exited) trades"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM trades
            WHERE exit_price IS NULL
            ORDER BY entry_time DESC
        """)
        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_today_trades(self) -> List[Dict]:
        """Get all trades from today"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM trades
            WHERE DATE(entry_time) = DATE('now')
            ORDER BY entry_time DESC
        """)
        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_today_pnl(self) -> float:
        """Get total P&L for today"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COALESCE(SUM(pnl), 0) as total_pnl
            FROM trades
            WHERE DATE(exit_time) = DATE('now') AND exit_price IS NOT NULL
        """)
        result = cursor.fetchone()
        conn.close()

        return result[0] if result else 0.0

    def get_recent_ticks(self, symbol: str, limit: int = 100, as_of_ts: int = None) -> List[Dict]:
        """Get recent ticks for a symbol.

        as_of_ts: when set, only return ticks with timestamp <= as_of_ts.
                  Used by backtest to avoid reading future data.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        if as_of_ts is not None:
            cursor.execute("""
                SELECT * FROM ticks
                WHERE symbol = ? AND timestamp <= ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (symbol, as_of_ts, limit))
        else:
            cursor.execute("""
                SELECT * FROM ticks
                WHERE symbol = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (symbol, limit))
        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def save_selected_stocks(self, stocks: List[str]):
        """Persist selected stocks to DB"""
        conn = self.get_connection()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('selected_stocks', ?)", (json.dumps(stocks),))
        conn.commit()
        conn.close()

    def get_selected_stocks(self) -> List[str]:
        """Load persisted stock selection"""
        conn = self.get_connection()
        row = conn.execute("SELECT value FROM settings WHERE key = 'selected_stocks'").fetchone()
        conn.close()
        return json.loads(row[0]) if row else []

    def get_trading_paused(self) -> bool:
        """Return True if trading is paused for today"""
        from datetime import date
        conn = self.get_connection()
        row = conn.execute("SELECT value FROM settings WHERE key = 'trading_paused'").fetchone()
        conn.close()
        if not row:
            return False
        import json
        data = json.loads(row[0])
        return data.get('paused', False) and data.get('date') == str(date.today())

    def set_trading_paused(self, paused: bool):
        """Persist trading paused state for today"""
        from datetime import date
        import json
        value = json.dumps({'paused': paused, 'date': str(date.today())})
        conn = self.get_connection()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('trading_paused', ?)", (value,))
        conn.commit()
        conn.close()

    def get_trade_by_id(self, trade_id: int) -> Optional[Dict]:
        """Get a specific trade by ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
        row = cursor.fetchone()
        conn.close()

        return dict(row) if row else None

# Global database instance
db = Database()
