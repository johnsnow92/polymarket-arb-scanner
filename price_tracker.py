"""Rolling price tracker with staleness detection across platforms.

Tracks the latest price per market per platform with timestamps and detects
when one platform's price has moved significantly while another platform's
price is stale (hasn't updated recently).
"""

import logging
import threading
import time

from config import STALE_PRICE_THRESHOLD, STALE_PRICE_MOVE_PCT

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PriceTracker
# ---------------------------------------------------------------------------

class PriceTracker:
    """Thread-safe rolling price tracker with cross-platform staleness detection.

    Maintains a nested mapping of (platform, market_key) -> (price, timestamp)
    and provides methods to detect when one platform's price is stale while
    another has recently moved -- a potential signal for arbitrage.

    Args:
        stale_threshold_seconds: How many seconds without an update before a
            price is considered stale.
        move_threshold_pct: Minimum absolute price difference (as a fraction,
            e.g. 0.03 = 3%) between a stale price and a fresh price to be
            flagged as an opportunity.
    """

    def __init__(
        self,
        stale_threshold_seconds: float = STALE_PRICE_THRESHOLD,
        move_threshold_pct: float = STALE_PRICE_MOVE_PCT,
    ) -> None:
        self._stale_threshold = stale_threshold_seconds
        self._move_threshold = move_threshold_pct
        # {market_key: {platform: (price, timestamp)}}
        self._prices: dict[str, dict[str, tuple[float, float]]] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    def update(self, platform: str, market_key: str, price: float) -> None:
        """Record a price update with the current timestamp.

        Args:
            platform: Platform identifier (e.g. "polymarket", "kalshi").
            market_key: Canonical market key shared across platforms.
            price: Latest price (typically 0.0 to 1.0 for prediction markets).
        """
        now = time.time()
        with self._lock:
            if market_key not in self._prices:
                self._prices[market_key] = {}
            self._prices[market_key][platform] = (price, now)
        logger.debug(
            "Price update: %s/%s = %.4f @ %.3f",
            platform, market_key, price, now,
        )

    def get_price(
        self, platform: str, market_key: str
    ) -> tuple[float, float] | None:
        """Return (price, timestamp) for a specific platform and market.

        Returns:
            A (price, timestamp) tuple, or None if no entry exists.
        """
        with self._lock:
            market_data = self._prices.get(market_key)
            if market_data is None:
                return None
            return market_data.get(platform)

    def get_all_prices(
        self, market_key: str
    ) -> dict[str, tuple[float, float]]:
        """Return all tracked prices for a market.

        Returns:
            Dict mapping platform -> (price, timestamp). Empty dict if the
            market has no tracked prices.
        """
        with self._lock:
            market_data = self._prices.get(market_key)
            if market_data is None:
                return {}
            return dict(market_data)

    def detect_stale_opportunities(self, market_key: str) -> list[dict]:
        """Find platforms with stale prices while another platform moved.

        For each platform whose price age exceeds `stale_threshold_seconds`,
        checks whether any other platform has a recent price that differs by
        more than `move_threshold_pct`. If so, yields an opportunity dict.

        Args:
            market_key: Canonical market key to check.

        Returns:
            List of dicts, each with keys:
                - stale_platform: Platform with the outdated price.
                - stale_price: Last known price on the stale platform.
                - stale_age_seconds: Seconds since the stale price was updated.
                - fresh_platform: Platform with a recent price update.
                - fresh_price: Current price on the fresh platform.
                - price_delta: fresh_price - stale_price (signed).
                - direction: "buy_yes" if stale is cheaper (stale < fresh),
                    "buy_no" if stale is more expensive (stale > fresh).
        """
        now = time.time()
        with self._lock:
            market_data = self._prices.get(market_key)
            if market_data is None:
                return []
            # Snapshot under lock to avoid holding it during iteration
            snapshot = dict(market_data)

        opportunities: list[dict] = []

        # Partition into stale and fresh
        stale_entries: list[tuple[str, float, float]] = []
        fresh_entries: list[tuple[str, float, float]] = []

        for platform, (price, ts) in snapshot.items():
            age = now - ts
            if age >= self._stale_threshold:
                stale_entries.append((platform, price, age))
            else:
                fresh_entries.append((platform, price, age))

        if not stale_entries or not fresh_entries:
            return []

        for stale_platform, stale_price, stale_age in stale_entries:
            for fresh_platform, fresh_price, _fresh_age in fresh_entries:
                delta = fresh_price - stale_price
                if abs(delta) < self._move_threshold:
                    continue

                direction = "buy_yes" if delta > 0 else "buy_no"

                opp = {
                    "stale_platform": stale_platform,
                    "stale_price": stale_price,
                    "stale_age_seconds": round(stale_age, 2),
                    "fresh_platform": fresh_platform,
                    "fresh_price": fresh_price,
                    "price_delta": round(delta, 6),
                    "direction": direction,
                }
                opportunities.append(opp)

                logger.info(
                    "Stale opportunity: %s (%.4f, %.1fs old) vs %s (%.4f), "
                    "delta=%.4f, direction=%s",
                    stale_platform, stale_price, stale_age,
                    fresh_platform, fresh_price, delta, direction,
                )

        return opportunities

    def cleanup(self, max_age_seconds: float = 300.0) -> None:
        """Remove entries older than max_age_seconds.

        Cleans up stale data to prevent unbounded memory growth. Markets with
        no remaining platform entries are removed entirely.

        Args:
            max_age_seconds: Maximum allowed age in seconds. Entries older
                than this are evicted.
        """
        now = time.time()
        cutoff = now - max_age_seconds
        removed = 0

        with self._lock:
            empty_markets: list[str] = []
            for market_key, platforms in self._prices.items():
                expired = [
                    p for p, (_price, ts) in platforms.items()
                    if ts < cutoff
                ]
                for platform in expired:
                    del platforms[platform]
                    removed += 1
                if not platforms:
                    empty_markets.append(market_key)
            for market_key in empty_markets:
                del self._prices[market_key]

        if removed:
            logger.debug(
                "Cleanup: removed %d stale entries (max_age=%.0fs)",
                removed, max_age_seconds,
            )
