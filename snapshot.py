"""Historical price snapshot recorder for backtesting."""

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class SnapshotRecorder:
    """Records price snapshots to SQLite for historical analysis and backtesting.

    Thread-safe with WAL mode, following the same pattern as TradeDB.
    """

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            data_dir = os.getenv("DATA_DIR", ".")
            db_path = os.path.join(data_dir, "snapshots.db")
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS price_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                market TEXT NOT NULL,
                platform_a TEXT,
                platform_b TEXT,
                price_a REAL,
                price_b REAL,
                gross_spread REAL,
                fees REAL,
                net_profit REAL,
                opp_type TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp
                ON price_snapshots(timestamp);

            CREATE INDEX IF NOT EXISTS idx_snapshots_opp_type
                ON price_snapshots(opp_type);

            -- PR E: auto-detected correlated pairs cached by
            -- correlation_tracker.py. ``pearson_r`` may be negative
            -- (anti-correlated pairs are still copy-tradeable signals).
            CREATE TABLE IF NOT EXISTS correlated_pairs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_a TEXT NOT NULL,
                market_b TEXT NOT NULL,
                pearson_r REAL NOT NULL,
                sample_count INTEGER NOT NULL,
                computed_at TEXT NOT NULL,
                UNIQUE(market_a, market_b)
            );

            CREATE INDEX IF NOT EXISTS idx_correlated_computed
                ON correlated_pairs(computed_at);
        """)
        # Add columns for Layer 2-4 strategy metadata (backward-compatible)
        for col, col_type in [
            ("direction", "TEXT"),
            ("confidence", "REAL"),
            ("strategy_layer", "INTEGER"),
        ]:
            try:
                self.conn.execute(
                    f"ALTER TABLE price_snapshots ADD COLUMN {col} {col_type}"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists
        self.conn.commit()

    # -----------------------------------------------------------------
    # Correlated-pairs cache (PR E — auto-correlation detection)
    # -----------------------------------------------------------------

    def upsert_correlated_pairs(self, pairs: list[dict]) -> int:
        """Replace the cached correlation set with ``pairs``.

        Each entry must have ``market_a``, ``market_b``, ``pearson_r``,
        ``sample_count``. ``computed_at`` is stamped server-side.

        Always replaces the entire cache — passing an empty list (or one
        whose entries all fail validation) clears the table so a stale
        result never lingers after the source data disappears.

        Returns the number of rows written.
        """
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for p in pairs or []:
            a = str(p.get("market_a", ""))
            b = str(p.get("market_b", ""))
            if not a or not b or a == b:
                continue
            # Canonicalise the pair so (a,b) and (b,a) collapse.
            a_canon, b_canon = sorted((a, b))
            rows.append((
                a_canon, b_canon,
                float(p.get("pearson_r", 0.0)),
                int(p.get("sample_count", 0)),
                now,
            ))
        with self._lock:
            self.conn.execute("DELETE FROM correlated_pairs")
            if rows:
                self.conn.executemany(
                    """INSERT OR REPLACE INTO correlated_pairs
                       (market_a, market_b, pearson_r, sample_count, computed_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    rows,
                )
            self.conn.commit()
        return len(rows)

    def get_correlated_pairs(
        self,
        min_abs_r: float = 0.0,
    ) -> list[dict]:
        """Return cached correlated pairs whose |pearson_r| >= ``min_abs_r``."""
        with self._lock:
            rows = self.conn.execute(
                """SELECT market_a, market_b, pearson_r, sample_count, computed_at
                   FROM correlated_pairs
                   WHERE ABS(pearson_r) >= ?
                   ORDER BY ABS(pearson_r) DESC""",
                (float(min_abs_r),),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_snapshot(self, opportunities: list[dict]) -> int:
        """Record a batch of opportunity price snapshots.

        Args:
            opportunities: List of opportunity dicts from the scanner.

        Returns:
            Number of snapshots recorded.
        """
        if not opportunities:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        rows = []

        for opp in opportunities:
            market = opp.get("market", "Unknown")
            opp_type = opp.get("type", "")
            net_profit = opp.get("net_profit", 0)

            # Extract platform info and prices
            platform_a, platform_b, price_a, price_b = self._extract_platforms(opp)

            # Parse total cost for gross spread calculation
            total_cost_str = opp.get("total_cost", "$0")
            if isinstance(total_cost_str, str):
                total_cost = float(total_cost_str.replace("$", ""))
            else:
                total_cost = float(total_cost_str)

            gross_spread = 1.0 - total_cost if total_cost < 1.0 else 0.0

            # Fees = gross_spread - net_profit
            fees = gross_spread - net_profit if gross_spread > net_profit else 0.0

            # Extract Layer 2-4 metadata
            direction = opp.get("_direction", "")
            confidence = opp.get("confidence")
            if isinstance(confidence, str):
                confidence = {"HIGH": 0.9, "MEDIUM": 0.7, "LOW": 0.5}.get(confidence)
            strategy_layer = self._get_strategy_layer(opp_type)

            rows.append((
                now, market, platform_a, platform_b,
                price_a, price_b, gross_spread, fees,
                net_profit, opp_type, direction, confidence, strategy_layer,
            ))

        if not rows:
            return 0

        with self._lock:
            self.conn.executemany(
                """INSERT INTO price_snapshots
                   (timestamp, market, platform_a, platform_b,
                    price_a, price_b, gross_spread, fees,
                    net_profit, opp_type, direction, confidence, strategy_layer)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            self.conn.commit()

        logger.debug("Recorded %d price snapshots.", len(rows))
        return len(rows)

    def _extract_platforms(self, opp: dict) -> tuple[str, str, float | None, float | None]:
        """Extract platform names and prices from an opportunity dict.

        Returns (platform_a, platform_b, price_a, price_b).
        """
        opp_type = opp.get("type", "")
        prices_str = opp.get("prices", "")

        # Cross-platform: "{platform}_Y={price} {platform}_N={price}"
        if "_platform_a" in opp:
            platform_a = opp.get("_platform_a", "")
            platform_b = opp.get("_platform_b", "")
            price_a = opp.get("_price_a")
            price_b = opp.get("_price_b")
            if price_a is None or price_b is None:
                price_a, price_b = self._parse_prices_str(prices_str)
            return platform_a, platform_b, price_a, price_b

        # Same-platform arbs
        if opp_type == "Binary" or opp_type.startswith("NegRisk"):
            return "polymarket", "polymarket", *self._parse_prices_str(prices_str)
        elif opp_type.startswith("Kalshi"):
            return "kalshi", "kalshi", *self._parse_prices_str(prices_str)
        elif opp_type.startswith("Betfair"):
            return "betfair", "betfair", *self._parse_prices_str(prices_str)
        elif opp_type.startswith("Smarkets"):
            return "smarkets", "smarkets", *self._parse_prices_str(prices_str)
        elif opp_type.startswith("SXBet"):
            return "sxbet", "sxbet", *self._parse_prices_str(prices_str)
        elif opp_type.startswith("Matchbook"):
            return "matchbook", "matchbook", *self._parse_prices_str(prices_str)
        elif opp_type.startswith("Gemini"):
            return "gemini", "gemini", *self._parse_prices_str(prices_str)
        elif opp_type.startswith("IBKR"):
            return "ibkr", "ibkr", *self._parse_prices_str(prices_str)
        elif opp_type.startswith("Cross"):
            # Standard cross: "PM_Y=0.300 K_N=0.350"
            return "polymarket", "kalshi", *self._parse_prices_str(prices_str)
        elif opp_type == "TriangularCross":
            pa = opp.get("_platform_a", "")
            pb = opp.get("_platform_b", "")
            return pa, pb, *self._parse_prices_str(prices_str)
        elif opp_type == "EventDivergence":
            platform = opp.get("_platform", "")
            return platform, "metaculus", *self._parse_prices_str(prices_str)
        elif opp_type == "StalePriceOpp":
            return (opp.get("_stale_platform", ""), opp.get("_fresh_platform", ""),
                    opp.get("_stale_price"), opp.get("_fresh_price"))
        elif opp_type == "ResolutionSnipeOpp":
            platform = opp.get("_platform", "")
            return platform, platform, opp.get("_price"), 1.0
        elif opp_type == "ConvergenceOpp":
            return (opp.get("_platform", ""), "median",
                    opp.get("_trade_price"), opp.get("_median_price"))
        elif opp_type == "MarketMake":
            platform = opp.get("_platform", "")
            return platform, platform, opp.get("_bid_price"), opp.get("_ask_price")
        elif opp_type.startswith("MultiCross"):
            return "multi", "cross", *self._parse_prices_str(prices_str)

        return "", "", None, None

    @staticmethod
    def _parse_prices_str(prices_str: str) -> tuple[float | None, float | None]:
        """Parse two prices from a prices string like 'Y=0.400 N=0.450'."""
        parts = prices_str.split()
        price_a = price_b = None
        for i, part in enumerate(parts):
            if "=" in part:
                try:
                    val = float(part.split("=", 1)[1])
                    if i == 0:
                        price_a = val
                    elif i == 1:
                        price_b = val
                except ValueError:
                    pass
            else:
                # Comma-separated (NegRisk): "0.20, 0.25, 0.30"
                try:
                    val = float(part.rstrip(","))
                    if price_a is None:
                        price_a = val
                    elif price_b is None:
                        price_b = val
                except ValueError:
                    pass
        return price_a, price_b

    def get_snapshots(
        self,
        start_time: str,
        end_time: str,
        opp_type: str | None = None,
    ) -> list[dict]:
        """Retrieve snapshots within a time range.

        Args:
            start_time: ISO format start timestamp.
            end_time: ISO format end timestamp.
            opp_type: Optional filter by opportunity type.

        Returns:
            List of snapshot dicts.
        """
        with self._lock:
            if opp_type:
                rows = self.conn.execute(
                    """SELECT * FROM price_snapshots
                       WHERE timestamp >= ? AND timestamp <= ? AND opp_type = ?
                       ORDER BY timestamp""",
                    (start_time, end_time, opp_type),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    """SELECT * FROM price_snapshots
                       WHERE timestamp >= ? AND timestamp <= ?
                       ORDER BY timestamp""",
                    (start_time, end_time),
                ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def _get_strategy_layer(opp_type: str) -> int:
        """Determine the strategy layer for an opportunity type."""
        _LAYERS = {
            "Binary": 1, "NegRisk": 1, "KalshiBinary": 1, "KalshiMulti": 1,
            "Cross": 1, "BetfairBack": 1, "SmarketsBack": 1, "SXBetBack": 1,
            "MatchbookBack": 1, "GeminiBinary": 1, "GeminiMulti": 1,
            "IBKRBinary": 1, "MultiCross": 1, "TriangularCross": 1,
            "Spread": 1,
            "StalePriceOpp": 2, "ResolutionSnipeOpp": 2,
            "MarketMake": 3,
            "EventDivergence": 4, "ConvergenceOpp": 4,
        }
        for prefix, layer in _LAYERS.items():
            if opp_type.startswith(prefix):
                return layer
        return 0

    def get_snapshot_count(self) -> int:
        """Return total number of snapshots in the database."""
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM price_snapshots"
            ).fetchone()
            return row["cnt"]

    def close(self):
        self.conn.close()
