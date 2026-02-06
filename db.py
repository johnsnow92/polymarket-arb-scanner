"""SQLite persistence for trade logging and opportunity tracking."""

import json
import sqlite3
from datetime import datetime, timezone


class TradeDB:
    """SQLite database for logging opportunities and trades."""

    def __init__(self, db_path: str = "trades.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                type TEXT NOT NULL,
                market TEXT NOT NULL,
                prices TEXT,
                total_cost REAL,
                net_profit REAL,
                net_roi REAL,
                depth REAL,
                action TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id INTEGER REFERENCES opportunities(id),
                timestamp TEXT NOT NULL,
                platform TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                status TEXT NOT NULL,
                fill_price REAL,
                order_id TEXT
            );
        """)
        self.conn.commit()

    def log_opportunity(
        self,
        opp_type: str,
        market: str,
        prices: str,
        total_cost: float,
        net_profit: float,
        net_roi: float,
        depth: float,
        action: str,
    ) -> int:
        """Log a detected opportunity. Returns the opportunity ID."""
        cur = self.conn.execute(
            """INSERT INTO opportunities
               (timestamp, type, market, prices, total_cost, net_profit, net_roi, depth, action)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                opp_type,
                market,
                prices,
                total_cost,
                net_profit,
                net_roi,
                depth,
                action,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def log_trade(
        self,
        opportunity_id: int,
        platform: str,
        side: str,
        price: float,
        size: float,
        status: str,
        fill_price: float | None = None,
        order_id: str | None = None,
    ) -> int:
        """Log a trade leg. Returns the trade ID."""
        cur = self.conn.execute(
            """INSERT INTO trades
               (opportunity_id, timestamp, platform, side, price, size, status, fill_price, order_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                opportunity_id,
                datetime.now(timezone.utc).isoformat(),
                platform,
                side,
                price,
                size,
                status,
                fill_price,
                order_id,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_trade_status(self, trade_id: int, status: str, fill_price: float | None = None):
        """Update the status of a trade leg."""
        if fill_price is not None:
            self.conn.execute(
                "UPDATE trades SET status = ?, fill_price = ? WHERE id = ?",
                (status, fill_price, trade_id),
            )
        else:
            self.conn.execute(
                "UPDATE trades SET status = ? WHERE id = ?",
                (status, trade_id),
            )
        self.conn.commit()

    def get_daily_pnl(self) -> float:
        """Get sum of net_profit for today's traded opportunities."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self.conn.execute(
            """SELECT COALESCE(SUM(net_profit), 0) as total
               FROM opportunities
               WHERE action = 'traded' AND timestamp LIKE ?""",
            (f"{today}%",),
        ).fetchone()
        return row["total"]

    def get_open_positions_count(self) -> int:
        """Count trades that are currently filled (not yet settled)."""
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM trades WHERE status = 'filled'"
        ).fetchone()
        return row["cnt"]

    def get_recent_opportunities(self, limit: int = 50) -> list[dict]:
        """Get recent opportunities."""
        rows = self.conn.execute(
            "SELECT * FROM opportunities ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_trades_for_opportunity(self, opportunity_id: int) -> list[dict]:
        """Get all trade legs for an opportunity."""
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE opportunity_id = ? ORDER BY id",
            (opportunity_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def is_market_active(self, market: str) -> bool:
        """Check if there's an active (non-settled) trade for this market."""
        row = self.conn.execute(
            """SELECT COUNT(*) as cnt FROM opportunities o
               JOIN trades t ON t.opportunity_id = o.id
               WHERE o.market = ? AND t.status = 'filled'""",
            (market,),
        ).fetchone()
        return row["cnt"] > 0

    def close(self):
        self.conn.close()
