"""Tests for Strategy #9: fee promotional arbitrage.

Covers:
- NearMissCache ring buffer behavior (capacity, TTL, overwrite)
- scan_fee_promo emits opps when fee rates drop
- scan_fee_promo emits nothing when nothing has changed
- config.get_promo_expiry parses dates and ignores garbage
- notifier.notify_promo_warning constructs a payload without crashing
"""

import sys, os
import time
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# NearMissCache
# ---------------------------------------------------------------------------

class TestNearMissCache:
    def _import(self):
        if "near_miss_cache" in sys.modules:
            del sys.modules["near_miss_cache"]
        from near_miss_cache import NearMissCache
        return NearMissCache

    def test_add_and_snapshot(self):
        NearMissCache = self._import()
        cache = NearMissCache(max_entries=10, ttl_seconds=60)
        cache.add({"_market_key": "m1", "net_profit": 0.01}, gap_to_threshold=0.005)
        cache.add({"_market_key": "m2", "net_profit": 0.02}, gap_to_threshold=0.001)
        snap = cache.snapshot()
        assert len(snap) == 2
        keys = {e["_market_key"] for e in snap}
        assert keys == {"m1", "m2"}

    def test_replaces_existing_entry_for_same_market(self):
        NearMissCache = self._import()
        cache = NearMissCache(max_entries=10, ttl_seconds=60)
        cache.add({"_market_key": "m1", "net_profit": 0.01}, 0.005)
        cache.add({"_market_key": "m1", "net_profit": 0.04}, 0.001)
        snap = cache.snapshot()
        assert len(snap) == 1
        assert snap[0]["net_profit"] == 0.04

    def test_evicts_oldest_at_capacity(self):
        NearMissCache = self._import()
        cache = NearMissCache(max_entries=2, ttl_seconds=60)
        cache.add({"_market_key": "a", "net_profit": 0.01}, 0.001)
        cache.add({"_market_key": "b", "net_profit": 0.02}, 0.001)
        cache.add({"_market_key": "c", "net_profit": 0.03}, 0.001)
        keys = {e["_market_key"] for e in cache.snapshot()}
        assert keys == {"b", "c"}

    def test_ttl_filters_expired(self):
        NearMissCache = self._import()
        cache = NearMissCache(max_entries=10, ttl_seconds=0.05)
        cache.add({"_market_key": "m1", "net_profit": 0.01}, 0.005)
        time.sleep(0.1)
        assert cache.snapshot() == []

    def test_skips_entries_without_market_key(self):
        NearMissCache = self._import()
        cache = NearMissCache()
        cache.add({"net_profit": 0.01}, 0.005)
        assert len(cache) == 0


# ---------------------------------------------------------------------------
# scan_fee_promo
# ---------------------------------------------------------------------------

class TestFeePromoScan:
    @pytest.fixture(autouse=True)
    def _isolate_modules(self):
        # Pop only modules we own — popping `fees` or `config` corrupts later
        # tests whose top-level imports bound names to the old module copy.
        for mod in ("near_miss_cache", "scans.fee_promo"):
            sys.modules.pop(mod, None)
        yield

    def _import(self):
        from near_miss_cache import NearMissCache
        from scans.fee_promo import scan_fee_promo
        return NearMissCache, scan_fee_promo

    def test_returns_empty_when_cache_empty(self):
        NearMissCache, scan_fee_promo = self._import()
        cache = NearMissCache()
        assert scan_fee_promo(cache=cache, min_profit=0.005) == []

    def test_emits_when_fee_drop_makes_arb_profitable(self, monkeypatch):
        # Stub fees.net_profit_cross_generic to behave like a configurable fn.
        # First call (during cache add) returns -0.01 (just barely failed).
        # Second call (during scan_fee_promo re-score) returns +0.05 (now profitable).
        import fees as fees_mod

        call_log = []

        def fake_net_profit(price_a, price_b, side_a, side_b, *, platform_a, platform_b):
            call_log.append((platform_a, platform_b))
            return {"net_profit": 0.05, "fees": 0.002, "gross_spread": 0.07}

        monkeypatch.setattr(fees_mod, "net_profit_cross_generic", fake_net_profit)

        NearMissCache, scan_fee_promo = self._import()
        cache = NearMissCache()
        cache.add({
            "_market_key": "mkt_xyz",
            "type": "Cross",
            "_platform_a": "polymarket",
            "_platform_b": "matchbook",
            "_price_a": 0.45,
            "_price_b": 0.50,
            "_side_a": "yes",
            "_side_b": "no",
            "net_profit": -0.01,
            "prices": "polymarket_Y=0.45 matchbook_N=0.50",
        }, gap_to_threshold=0.02)

        opps = scan_fee_promo(cache=cache, min_profit=0.005)
        assert len(opps) == 1
        assert opps[0]["type"] == "FeePromo"
        assert opps[0]["_layer"] == 2
        assert opps[0]["net_profit"] == 0.05
        assert opps[0]["_promo_uplift"] == pytest.approx(0.05 - (-0.01))

    def test_skips_entries_below_min_profit(self, monkeypatch):
        import fees as fees_mod
        monkeypatch.setattr(
            fees_mod, "net_profit_cross_generic",
            lambda *a, **kw: {"net_profit": 0.001, "fees": 0.0, "gross_spread": 0.01},
        )
        NearMissCache, scan_fee_promo = self._import()
        cache = NearMissCache()
        cache.add({
            "_market_key": "mkt_low",
            "type": "Cross",
            "_platform_a": "polymarket", "_platform_b": "kalshi",
            "_price_a": 0.45, "_price_b": 0.55,
            "_side_a": "yes", "_side_b": "no",
            "net_profit": -0.02,
        }, gap_to_threshold=0.03)
        assert scan_fee_promo(cache=cache, min_profit=0.005) == []

    def test_skips_malformed_entries(self):
        NearMissCache, scan_fee_promo = self._import()
        cache = NearMissCache()
        cache.add({
            "_market_key": "mkt_broken",
            # Missing _platform_a / _price_a → re-score gracefully skips
            "net_profit": -0.01,
        }, gap_to_threshold=0.02)
        assert scan_fee_promo(cache=cache, min_profit=0.005) == []


# ---------------------------------------------------------------------------
# config.get_promo_expiry
# ---------------------------------------------------------------------------

class TestPromoExpiry:
    """Tests that pop config to re-read env vars; teardown re-pops so the
    suite picks up the original (unpatched) environment afterwards.
    """

    @pytest.fixture(autouse=True)
    def _config_isolation(self):
        sys.modules.pop("config", None)
        yield
        sys.modules.pop("config", None)

    def test_parses_iso_date(self, monkeypatch):
        monkeypatch.setenv("MATCHBOOK_PROMO_EXPIRES", "2026-08-15")
        from config import get_promo_expiry
        from datetime import date
        assert get_promo_expiry("matchbook") == date(2026, 8, 15)
        assert get_promo_expiry("MATCHBOOK") == date(2026, 8, 15)

    def test_returns_none_for_empty(self, monkeypatch):
        monkeypatch.delenv("GEMINI_PROMO_EXPIRES", raising=False)
        from config import get_promo_expiry
        assert get_promo_expiry("gemini") is None

    def test_returns_none_for_invalid_date(self, monkeypatch):
        monkeypatch.setenv("POLYMARKET_PROMO_EXPIRES", "tomorrow")
        from config import get_promo_expiry
        assert get_promo_expiry("polymarket") is None

    def test_returns_none_for_unknown_platform(self):
        from config import get_promo_expiry
        assert get_promo_expiry("kalshi") is None


# ---------------------------------------------------------------------------
# notifier.notify_promo_warning
# ---------------------------------------------------------------------------

class TestPromoWarning:
    def test_no_op_without_url(self):
        from notifier import WebhookNotifier
        n = WebhookNotifier(url="")
        # Should return without raising
        n.notify_promo_warning("matchbook", days_remaining=3)

    def test_telegram_path_dispatches(self, monkeypatch):
        from notifier import WebhookNotifier
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
        n = WebhookNotifier(url="telegram://")
        sent = []
        n._send_telegram = lambda text: sent.append(text)
        # Patch threading.Thread to invoke target inline for deterministic test
        import notifier as notifier_mod
        orig_thread = notifier_mod.threading.Thread

        class InlineThread:
            def __init__(self, target=None, args=(), daemon=None):
                self._target = target
                self._args = args
            def start(self):
                self._target(*self._args)
        monkeypatch.setattr(notifier_mod.threading, "Thread", InlineThread)
        n.notify_promo_warning("gemini", days_remaining=2, expiry_iso="2026-06-01")
        assert len(sent) == 1
        assert "gemini" in sent[0]
        assert "2 day" in sent[0]
        monkeypatch.setattr(notifier_mod.threading, "Thread", orig_thread)
