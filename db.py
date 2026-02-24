"""SQLite persistence for trade logging and opportunity tracking."""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class TradeDB:
    """Thread-safe SQLite database for logging opportunities and trades."""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            import os
            data_dir = os.getenv("DATA_DIR", ".")
            db_path = os.path.join(data_dir, "trades.db")
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL mode allows concurrent reads while writing
        self.conn.execute("PRAGMA journal_mode=WAL")
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

            CREATE TABLE IF NOT EXISTS partial_fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER REFERENCES trades(id),
                opportunity_id INTEGER REFERENCES opportunities(id),
                platform TEXT NOT NULL,
                token_id TEXT,
                side TEXT NOT NULL,
                fill_price REAL NOT NULL,
                size REAL NOT NULL,
                hedge_status TEXT NOT NULL DEFAULT 'pending',
                hedge_attempts INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
        """)
        self.conn.commit()

        # Safe migration: add slippage column if it doesn't exist
        try:
            self.conn.execute("ALTER TABLE trades ADD COLUMN slippage REAL")
            self.conn.commit()
        except sqlite3.OperationalError:
            logger.debug("Migration: column already exists")

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
    ) -> int | None:
        """Log a detected opportunity. Returns the opportunity ID."""
        with self._lock:
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
    ) -> int | None:
        """Log a trade leg. Returns the trade ID."""
        with self._lock:
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

    def update_trade_status(self, trade_id: int, status: str, fill_price: float | None = None,
                            slippage: float | None = None):
        """Update the status of a trade leg, optionally with fill price and slippage."""
        with self._lock:
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
            if slippage is not None:
                self.conn.execute(
                    "UPDATE trades SET slippage = ? WHERE id = ?",
                    (slippage, trade_id),
                )
            self.conn.commit()

    def get_daily_pnl(self) -> float:
        """Get realized P&L from positions settled today, plus expected P&L from open positions today."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(realized_pnl), 0) as total
                   FROM positions
                   WHERE status = 'settled' AND settlement_timestamp LIKE ?""",
                (f"{today}%",),
            ).fetchone()
            realized = row["total"]
            row2 = self.conn.execute(
                """SELECT COALESCE(SUM(expected_pnl), 0) as total
                   FROM positions
                   WHERE status = 'open' AND entry_timestamp LIKE ?""",
                (f"{today}%",),
            ).fetchone()
            return realized + row2["total"]

    def get_open_positions_count(self) -> int:
        """Count positions that are currently open (not yet settled)."""
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM positions WHERE status = 'open'"
            ).fetchone()
            return row["cnt"]

    def get_recent_opportunities(self, limit: int = 50) -> list[dict]:
        """Get recent opportunities."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM opportunities ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_trades_for_opportunity(self, opportunity_id: int) -> list[dict]:
        """Get all trade legs for an opportunity."""
        with self._lock:
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
    ) -> int | None:
        """Create an open position after a trade is filled. Returns position ID."""
        with self._lock:
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
        with self._lock:
            self.conn.execute(
                """UPDATE positions
                   SET status = ?, realized_pnl = ?, settlement_timestamp = ?
                   WHERE id = ?""",
                (status, realized_pnl, datetime.now(timezone.utc).isoformat(), position_id),
            )
            self.conn.commit()

    def get_open_positions(self) -> list[dict]:
        """Get all open positions."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM positions WHERE status = 'open' ORDER BY entry_timestamp"
            ).fetchall()
            return [dict(r) for r in rows]

    def is_market_active(self, market: str) -> bool:
        """Check if there's an open position for this market."""
        with self._lock:
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
        with self._lock:
            row = self.conn.execute(
                """SELECT MAX(expected_pnl) as best_pnl FROM positions
                   WHERE market_identifier = ? AND status = 'open'""",
                (market,),
            ).fetchone()
            return row["best_pnl"] if row and row["best_pnl"] is not None else None

    def get_pending_trades(self) -> list[dict]:
        """Get trades with status 'pending' (may be orphaned from a crash)."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM trades WHERE status = 'pending' ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_open_positions_with_trades(self) -> list[dict]:
        """Get open positions with their associated trade order IDs.

        Returns positions joined with their trade legs so crash recovery
        can check order status on each platform.
        """
        with self._lock:
            rows = self.conn.execute(
                """SELECT p.*, t.platform as trade_platform, t.order_id, t.status as trade_status
                   FROM positions p
                   LEFT JOIN trades t ON t.opportunity_id = p.opportunity_id
                   WHERE p.status = 'open'
                   ORDER BY p.id, t.id"""
            ).fetchall()
            return [dict(r) for r in rows]

    def get_avg_slippage(self) -> float:
        """Get average slippage across all trades that have slippage data."""
        with self._lock:
            row = self.conn.execute(
                "SELECT AVG(slippage) as avg_slip FROM trades WHERE slippage IS NOT NULL"
            ).fetchone()
            return row["avg_slip"] if row and row["avg_slip"] is not None else 0.0

    def log_partial_fill(
        self,
        trade_id: int,
        opportunity_id: int,
        platform: str,
        token_id: str,
        side: str,
        fill_price: float,
        size: float,
    ) -> int | None:
        """Log a partial fill for hedging. Returns partial_fill ID."""
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO partial_fills
                   (trade_id, opportunity_id, platform, token_id, side, fill_price, size, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (trade_id, opportunity_id, platform, token_id, side, fill_price, size,
                 datetime.now(timezone.utc).isoformat()),
            )
            self.conn.commit()
            return cur.lastrowid

    def get_pending_partial_fills(self) -> list[dict]:
        """Get partial fills awaiting hedge."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM partial_fills WHERE hedge_status = 'pending' ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    def update_partial_fill(self, pf_id: int, status: str, attempts: int | None = None):
        """Update partial fill hedge status."""
        with self._lock:
            if attempts is not None:
                self.conn.execute(
                    "UPDATE partial_fills SET hedge_status = ?, hedge_attempts = ? WHERE id = ?",
                    (status, attempts, pf_id),
                )
            else:
                self.conn.execute(
                    "UPDATE partial_fills SET hedge_status = ? WHERE id = ?",
                    (status, pf_id),
                )
            if status in ("hedged", "failed"):
                self.conn.execute(
                    "UPDATE partial_fills SET resolved_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), pf_id),
                )
            self.conn.commit()

    # ---------------------------------------------------------------------------
    # Dashboard queries
    # ---------------------------------------------------------------------------

    def get_daily_pnl_history(self, days: int = 30) -> list[dict]:
        """Get daily realized P&L aggregated by date for the last N days.

        Returns:
            List of dicts with keys 'date' (YYYY-MM-DD) and 'pnl' (float).
        """
        with self._lock:
            rows = self.conn.execute(
                """SELECT date(settlement_timestamp) as date,
                          SUM(realized_pnl) as pnl
                   FROM positions
                   WHERE status = 'settled'
                     AND settlement_timestamp >= date('now', ?)
                   GROUP BY date(settlement_timestamp)
                   ORDER BY date""",
                (f"-{days} days",),
            ).fetchall()
            return [{"date": r["date"], "pnl": round(r["pnl"], 4)} for r in rows]

    def get_positions_by_platform(self) -> list[dict]:
        """Get open position counts and total expected P&L grouped by platform.

        Returns:
            List of dicts with keys 'platform', 'count', 'total_expected_pnl'.
        """
        with self._lock:
            rows = self.conn.execute(
                """SELECT platform,
                          COUNT(*) as count,
                          COALESCE(SUM(expected_pnl), 0) as total_expected_pnl
                   FROM positions
                   WHERE status = 'open'
                   GROUP BY platform
                   ORDER BY count DESC"""
            ).fetchall()
            return [
                {
                    "platform": r["platform"],
                    "count": r["count"],
                    "total_expected_pnl": round(r["total_expected_pnl"], 4),
                }
                for r in rows
            ]

    def get_opportunity_stats_by_type(self) -> list[dict]:
        """Get opportunity statistics grouped by type.

        Returns:
            List of dicts with type, count, avg_roi, avg_profit, total_profit.
        """
        with self._lock:
            rows = self.conn.execute(
                """SELECT type,
                          COUNT(*) as count,
                          AVG(net_roi) as avg_roi,
                          AVG(net_profit) as avg_profit,
                          SUM(net_profit) as total_profit
                   FROM opportunities
                   GROUP BY type
                   ORDER BY count DESC"""
            ).fetchall()
            return [
                {
                    "type": r["type"],
                    "count": r["count"],
                    "avg_roi": round(r["avg_roi"] or 0, 4),
                    "avg_profit": round(r["avg_profit"] or 0, 4),
                    "total_profit": round(r["total_profit"] or 0, 4),
                }
                for r in rows
            ]

    def get_recent_trades(self, limit: int = 100) -> list[dict]:
        """Get recent trades with their opportunity context.

        Returns:
            List of trade dicts enriched with opportunity type and market.
        """
        with self._lock:
            rows = self.conn.execute(
                """SELECT t.*, o.type as opp_type, o.market as opp_market
                   FROM trades t
                   LEFT JOIN opportunities o ON t.opportunity_id = o.id
                   ORDER BY t.id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_cumulative_pnl(self) -> float:
        """Get total realized P&L across all settled positions."""
        with self._lock:
            row = self.conn.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) as total FROM positions WHERE status = 'settled'"
            ).fetchone()
            return round(row["total"], 4)

    def close(self):
        self.conn.close()
