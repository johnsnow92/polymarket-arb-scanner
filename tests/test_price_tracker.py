"""Dedicated tests for price_tracker.py — rolling cross-platform staleness.

Coverage:
- update / get_price / get_all_prices basic CRUD
- detect_stale_opportunities pairs stale + fresh entries with direction inference
- detect_stale_opportunities returns empty when all fresh or all stale
- move-threshold gating
- cleanup() evicts old entries and empty markets
- Direction inference (buy_yes when stale < fresh, buy_no when stale > fresh)
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from price_tracker import PriceTracker


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestUpdateAndGet:
    def test_update_then_get(self):
        tr = PriceTracker()
        tr.update("polymarket", "m1", 0.55)
        result = tr.get_price("polymarket", "m1")
        assert result is not None
        price, ts = result
        assert price == 0.55
        assert ts > 0

    def test_get_missing_returns_none(self):
        tr = PriceTracker()
        assert tr.get_price("polymarket", "absent") is None

    def test_get_all_prices_empty(self):
        tr = PriceTracker()
        assert tr.get_all_prices("absent") == {}

    def test_get_all_prices_multiple_platforms(self):
        tr = PriceTracker()
        tr.update("polymarket", "m1", 0.50)
        tr.update("kalshi", "m1", 0.52)
        tr.update("gemini", "m1", 0.54)
        all_prices = tr.get_all_prices("m1")
        assert len(all_prices) == 3
        assert "polymarket" in all_prices
        assert all_prices["kalshi"][0] == 0.52

    def test_update_replaces_previous_entry(self):
        tr = PriceTracker()
        tr.update("polymarket", "m1", 0.50)
        first_ts = tr.get_price("polymarket", "m1")[1]
        time.sleep(0.01)
        tr.update("polymarket", "m1", 0.60)
        second = tr.get_price("polymarket", "m1")
        assert second[0] == 0.60
        assert second[1] >= first_ts


# ---------------------------------------------------------------------------
# detect_stale_opportunities
# ---------------------------------------------------------------------------


class TestDetectStale:
    def _make_tracker_with_stale_fresh_pair(
        self,
        stale_price: float = 0.45,
        fresh_price: float = 0.55,
        stale_seconds_old: float = 10.0,
        stale_threshold: float = 5.0,
        move_threshold: float = 0.03,
    ) -> PriceTracker:
        tr = PriceTracker(
            stale_threshold_seconds=stale_threshold,
            move_threshold_pct=move_threshold,
        )
        # Manually plant the entries so we can control timestamps.
        now = time.time()
        tr._prices["m1"] = {
            "polymarket": (stale_price, now - stale_seconds_old),
            "kalshi": (fresh_price, now),
        }
        return tr

    def test_flags_stale_when_fresh_moved_up(self):
        tr = self._make_tracker_with_stale_fresh_pair(
            stale_price=0.45, fresh_price=0.55,
        )
        opps = tr.detect_stale_opportunities("m1")
        assert len(opps) == 1
        opp = opps[0]
        assert opp["stale_platform"] == "polymarket"
        assert opp["fresh_platform"] == "kalshi"
        assert opp["direction"] == "buy_yes"  # stale 0.45 < fresh 0.55
        assert opp["price_delta"] == 0.10

    def test_flags_stale_when_fresh_moved_down(self):
        tr = self._make_tracker_with_stale_fresh_pair(
            stale_price=0.65, fresh_price=0.50,
        )
        opps = tr.detect_stale_opportunities("m1")
        assert len(opps) == 1
        assert opps[0]["direction"] == "buy_no"  # stale 0.65 > fresh 0.50
        assert opps[0]["price_delta"] == -0.15

    def test_below_move_threshold_no_opp(self):
        tr = self._make_tracker_with_stale_fresh_pair(
            stale_price=0.50, fresh_price=0.51,  # 1c move, threshold 3c
            move_threshold=0.03,
        )
        assert tr.detect_stale_opportunities("m1") == []

    def test_all_fresh_no_opp(self):
        tr = PriceTracker(stale_threshold_seconds=60.0)
        tr.update("polymarket", "m1", 0.50)
        tr.update("kalshi", "m1", 0.55)
        # Both updated just now → both fresh.
        assert tr.detect_stale_opportunities("m1") == []

    def test_all_stale_no_opp(self):
        tr = PriceTracker(stale_threshold_seconds=1.0)
        now = time.time()
        tr._prices["m1"] = {
            "polymarket": (0.50, now - 30),
            "kalshi": (0.55, now - 30),
        }
        # Both stale → no fresh side to compare against.
        assert tr.detect_stale_opportunities("m1") == []

    def test_returns_empty_for_unknown_market(self):
        tr = PriceTracker()
        assert tr.detect_stale_opportunities("absent") == []

    def test_stale_age_in_result(self):
        tr = self._make_tracker_with_stale_fresh_pair(
            stale_seconds_old=12.5, stale_threshold=5.0,
        )
        opp = tr.detect_stale_opportunities("m1")[0]
        assert opp["stale_age_seconds"] >= 12.0
        assert opp["stale_age_seconds"] <= 13.5


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_removes_old_entries(self):
        tr = PriceTracker()
        now = time.time()
        tr._prices["m1"] = {
            "polymarket": (0.50, now - 600),  # old
            "kalshi": (0.55, now),            # fresh
        }
        tr.cleanup(max_age_seconds=300.0)
        all_p = tr.get_all_prices("m1")
        assert "polymarket" not in all_p
        assert "kalshi" in all_p

    def test_removes_empty_market(self):
        tr = PriceTracker()
        now = time.time()
        tr._prices["m1"] = {
            "polymarket": (0.50, now - 600),
            "kalshi": (0.55, now - 600),
        }
        tr.cleanup(max_age_seconds=300.0)
        assert tr.get_all_prices("m1") == {}
        # Market key itself should also be evicted.
        assert "m1" not in tr._prices

    def test_keeps_fresh_unchanged(self):
        tr = PriceTracker()
        tr.update("polymarket", "m1", 0.5)
        tr.cleanup(max_age_seconds=300.0)
        assert tr.get_price("polymarket", "m1") is not None
