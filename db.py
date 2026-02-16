"""SQLite persistence for trade logging and opportunity tracking."""

import json
import sqlite3
from datetime import datetime, timezone


class TradeDB:
    """SQLite database for logging opportunities and trades."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            import os
            data_dir = os.getenv("DATA_DIR", ".")
            db_path = os.path.join(data_dir, "trades.db")
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

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id INTEGER REFERENCES opportunities(id),
                market_identifier TEXT NOT NULL,
                platform TEXT NOT NULL,
                entry_timestamp TEXT NOT NULL,
                settlement_timestamp TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                realized_pnl REAL,
                expected_pnl REAL
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
        """Get realized P&L from positions settled today, plus expected P&L from open positions today."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Sum realized P&L from settled positions today
        row = self.conn.execute(
            """SELECT COALESCE(SUM(realized_pnl), 0) as total
               FROM positions
               WHERE status = 'settled' AND settlement_timestamp LIKE ?""",
            (f"{today}%",),
        ).fetchone()
        realized = row["total"]
        # Also include expected P&L from positions opened today (not yet settled)
        row2 = self.conn.execute(
            """SELECT COALESCE(SUM(expected_pnl), 0) as total
               FROM positions
               WHERE status = 'open' AND entry_timestamp LIKE ?""",
            (f"{today}%",),
        ).fetchone()
        return realized + row2["total"]

    def get_open_positions_count(self) -> int:
        """Count positions that are currently open (not yet settled)."""
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM positions WHERE status = 'open'"
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

    def create_position(
        self,
        opportunity_id: int,
        market_identifier: str,
        platform: str,
        expected_pnl: float,
    ) -> int:
        """Create an open position after a trade is filled. Returns position ID."""
        cur = self.conn.execute(
            """INSERT INTO positions
               (opportunity_id, market_identifier, platform, entry_timestamp, status, expected_pnl)
               VALUES (?, ?, ?, ?, 'open', ?)""",
            (
                opportunity_id,
                market_identifier,
                platform,
                datetime.now(timezone.utc).isoformat(),
                expected_pnl,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def settle_position(self, position_id: int, realized_pnl: float, status: str = "settled"):
        """Mark a position as settled with realized P&L."""
        self.conn.execute(
            """UPDATE positions
               SET status = ?, realized_pnl = ?, settlement_timestamp = ?
               WHERE id = ?""",
            (status, realized_pnl, datetime.now(timezone.utc).isoformat(), position_id),
        )
        self.conn.commit()

    def get_open_positions(self) -> list[dict]:
        """Get all open positions."""
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE status = 'open' ORDER BY entry_timestamp"
        ).fetchall()
        return [dict(r) for r in rows]

    def is_market_active(self, market: str) -> bool:
        """Check if there's an open position for this market."""
        row = self.conn.execute(
            """SELECT COUNT(*) as cnt FROM positions
               WHERE market_identifier = ? AND status = 'open'""",
            (market,),
        ).fetchone()
        return row["cnt"] > 0

    def get_active_market_expected_pnl(self, market: str) -> float | None:
        """Get the expected P&L of the best open position for this market.

        Returns None if no open position exists.
        """
        row = self.conn.execute(
            """SELECT MAX(expected_pnl) as best_pnl FROM positions
               WHERE market_identifier = ? AND status = 'open'""",
            (market,),
        ).fetchone()
        return row["best_pnl"] if row and row["best_pnl"] is not None else None

    def get_pending_trades(self) -> list[dict]:
        """Get trades with status 'pending' (may be orphaned from a crash)."""
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE status = 'pending' ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_open_positions_with_trades(self) -> list[dict]:
        """Get open positions with their associated trade order IDs.

        Returns positions joined with their trade legs so crash recovery
        can check order status on each platform.
        """
        rows = self.conn.execute(
            """SELECT p.*, t.platform as trade_platform, t.order_id, t.status as trade_status
               FROM positions p
               LEFT JOIN trades t ON t.opportunity_id = p.opportunity_id
               WHERE p.status = 'open'
               ORDER BY p.id, t.id"""
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.conn.close()
