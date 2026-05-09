"""Ring buffer of cross-platform arbs that just barely missed MIN_NET_ROI.

Used by Strategy #9 (fee promotional arbitrage). When `_refine_cross_with_clob`
drops a candidate because net profit is below the threshold but within
``PROMO_NEAR_MISS_BAND`` of it, the dropped opp is appended here. Later, when
fee rates drop (e.g. a Matchbook 0% promo activates) and `reload_fee_rates`
detects the change, ``scans.fee_promo.scan_fee_promo`` re-scores every entry
with current fees and emits any that now clear the threshold as
``FeePromo`` opportunities.

Thread-safe. Keyed by `_market_key` so a fresher near-miss replaces a stale
one for the same market rather than allowing duplicates to accumulate.
"""

import threading
import time

DEFAULT_MAX_ENTRIES = 500
DEFAULT_TTL_SECONDS = 3600  # Drop entries older than 1 hour


class NearMissCache:
    """Ring buffer of cross-platform near-miss opportunities.

    Each entry is a copy of the dropped opp dict plus a timestamp. Insertion
    order is preserved so eviction at capacity removes the oldest entry.
    Entries older than ``ttl_seconds`` are pruned lazily on read.
    """

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES,
                 ttl_seconds: float = DEFAULT_TTL_SECONDS):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: dict[str, dict] = {}
        self._lock = threading.Lock()

    def add(self, opp: dict, gap_to_threshold: float) -> None:
        """Record a near-miss arbitrage candidate.

        Args:
            opp: The original opportunity dict that failed MIN_NET_ROI.
                Should include `_market_key`, `type`, the prices, the
                computed `net_profit`, and platform identifiers.
            gap_to_threshold: How far below the threshold the opp landed
                (positive number, in dollars or ROI fraction depending on
                caller convention — kept opaque here).
        """
        key = opp.get("_market_key") or opp.get("market") or ""
        if not key:
            return
        record = {
            **opp,
            "_near_miss_ts": time.time(),
            "_near_miss_gap": gap_to_threshold,
        }
        with self._lock:
            # Replace existing entry for the same market (newer wins)
            if key in self._entries:
                self._entries.pop(key)
            self._entries[key] = record
            # Evict oldest entries past capacity
            while len(self._entries) > self.max_entries:
                oldest_key = next(iter(self._entries))
                self._entries.pop(oldest_key)

    def snapshot(self) -> list[dict]:
        """Return a list copy of currently-valid entries (TTL filtered)."""
        cutoff = time.time() - self.ttl_seconds
        with self._lock:
            valid = [v for v in self._entries.values()
                     if v.get("_near_miss_ts", 0) >= cutoff]
            # Prune expired in place to keep dict small
            expired_keys = [k for k, v in self._entries.items()
                            if v.get("_near_miss_ts", 0) < cutoff]
            for k in expired_keys:
                self._entries.pop(k, None)
            return list(valid)

    def clear(self) -> int:
        """Drop all entries. Returns count removed (used in tests)."""
        with self._lock:
            n = len(self._entries)
            self._entries.clear()
            return n

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# Module-level singleton — `scan_cross_*` writes here, `scan_fee_promo` reads.
# A single shared instance is the simplest contract; tests can swap it via
# `near_miss_cache._GLOBAL = NearMissCache()` to isolate state.
_GLOBAL = NearMissCache()


def get_global_cache() -> NearMissCache:
    """Return the process-global near-miss cache."""
    return _GLOBAL


def reset_global_cache() -> None:
    """Replace the global cache with a fresh one. Test helper."""
    global _GLOBAL
    _GLOBAL = NearMissCache()
