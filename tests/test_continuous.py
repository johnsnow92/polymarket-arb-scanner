"""Tests for continuous.py — OpportunityIndex and WS-triggered execution logic."""

import sys
import os
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock, patch

# Save any real modules already loaded so we can restore them after import
_saved_modules = {}
_modules_to_mock = [
    "kalshi_api", "polymarket_api", "dashboard", "display", "recovery",
]
for _mod_name in _modules_to_mock:
    if _mod_name in sys.modules:
        _saved_modules[_mod_name] = sys.modules[_mod_name]

# Mock heavy dependencies before importing continuous
mock_kalshi = MagicMock()
mock_kalshi._sign_pss = MagicMock(return_value="fake_sig")
mock_kalshi._load_private_key = MagicMock(return_value=None)
mock_kalshi.KALSHI_BASE_URL = "https://api.elections.kalshi.com"
mock_kalshi.KALSHI_API_PATH = "/trade-api/v2"
sys.modules["kalshi_api"] = mock_kalshi

mock_pm = MagicMock()
sys.modules["polymarket_api"] = mock_pm

mock_dashboard = MagicMock()
mock_dashboard.state = MagicMock()
sys.modules["dashboard"] = mock_dashboard

sys.modules["display"] = MagicMock()
sys.modules["recovery"] = MagicMock()

from continuous import OpportunityIndex, _recalc_profit, _get_market_lock, _calc_realized_pnl
from db import TradeDB

# Restore original modules so other test files are not affected
for _mod_name in _modules_to_mock:
    if _mod_name in _saved_modules:
        sys.modules[_mod_name] = _saved_modules[_mod_name]
    elif _mod_name in sys.modules:
        del sys.modules[_mod_name]


# ---------------------------------------------------------------------------
# OpportunityIndex — rebuild and lookup
# ---------------------------------------------------------------------------


class TestOpportunityIndexRebuild:
    def test_rebuild_indexes_polymarket_tokens(self):
        idx = OpportunityIndex()
        opps = [
            {"_token_ids": ["tok_a", "tok_b"], "net_profit": 0.05},
        ]
        idx.rebuild(opps)

        result = idx.lookup("polymarket", "tok_a")
        assert len(result) == 1
        assert result[0]["net_profit"] == 0.05

        result_b = idx.lookup("polymarket", "tok_b")
        assert len(result_b) == 1

    def test_rebuild_indexes_kalshi_ticker(self):
        idx = OpportunityIndex()
        opps = [
            {"_kalshi_ticker": "PRES-2028-REP", "net_profit": 0.03},
        ]
        idx.rebuild(opps)

        result = idx.lookup("kalshi", "PRES-2028-REP")
        assert len(result) == 1
        assert result[0]["net_profit"] == 0.03

    def test_rebuild_indexes_kalshi_tickers_list(self):
        idx = OpportunityIndex()
        opps = [
            {"_kalshi_tickers": ["TICK-A", "TICK-B"], "net_profit": 0.02},
        ]
        idx.rebuild(opps)

        assert len(idx.lookup("kalshi", "TICK-A")) == 1
        assert len(idx.lookup("kalshi", "TICK-B")) == 1

    def test_rebuild_replaces_previous_index(self):
        idx = OpportunityIndex()
        idx.rebuild([{"_token_ids": ["old_tok"], "net_profit": 0.01}])
        assert len(idx.lookup("polymarket", "old_tok")) == 1

        idx.rebuild([{"_token_ids": ["new_tok"], "net_profit": 0.02}])
        assert len(idx.lookup("polymarket", "old_tok")) == 0
        assert len(idx.lookup("polymarket", "new_tok")) == 1

    def test_rebuild_empty_list_clears_index(self):
        idx = OpportunityIndex()
        idx.rebuild([{"_token_ids": ["tok"], "net_profit": 0.01}])
        assert len(idx.lookup("polymarket", "tok")) == 1

        idx.rebuild([])
        assert len(idx.lookup("polymarket", "tok")) == 0

    def test_multiple_opps_same_token(self):
        idx = OpportunityIndex()
        opps = [
            {"_token_ids": ["shared_tok"], "net_profit": 0.05},
            {"_token_ids": ["shared_tok"], "net_profit": 0.08},
        ]
        idx.rebuild(opps)

        result = idx.lookup("polymarket", "shared_tok")
        assert len(result) == 2

    def test_opp_with_no_keys_not_indexed(self):
        idx = OpportunityIndex()
        opps = [
            {"net_profit": 0.05},  # no tokens or tickers
        ]
        idx.rebuild(opps)
        assert idx.lookup("polymarket", "") == []
        assert idx.lookup("kalshi", "") == []


# ---------------------------------------------------------------------------
# OpportunityIndex — lookup
# ---------------------------------------------------------------------------


class TestOpportunityIndexLookup:
    def test_lookup_returns_empty_for_unknown_token(self):
        idx = OpportunityIndex()
        idx.rebuild([{"_token_ids": ["tok_a"], "net_profit": 0.01}])

        assert idx.lookup("polymarket", "tok_nonexistent") == []

    def test_lookup_returns_empty_for_wrong_platform(self):
        idx = OpportunityIndex()
        idx.rebuild([{"_token_ids": ["tok_a"], "net_profit": 0.01}])

        assert idx.lookup("kalshi", "tok_a") == []

    def test_lookup_returns_list_copy(self):
        """Ensure returned list is a copy, not a reference to internal data."""
        idx = OpportunityIndex()
        idx.rebuild([{"_token_ids": ["tok_a"], "net_profit": 0.01}])

        result1 = idx.lookup("polymarket", "tok_a")
        result2 = idx.lookup("polymarket", "tok_a")
        assert result1 is not result2


# ---------------------------------------------------------------------------
# OpportunityIndex — get_subscription_tokens
# ---------------------------------------------------------------------------


class TestGetSubscriptionTokens:
    def test_returns_poly_and_kalshi_tokens(self):
        idx = OpportunityIndex()
        opps = [
            {"_token_ids": ["poly_1"], "net_profit": 0.05},
            {"_kalshi_ticker": "KALSHI-1", "net_profit": 0.03},
        ]
        idx.rebuild(opps)

        poly, kalshi = idx.get_subscription_tokens(limit=10)
        assert "poly_1" in poly
        assert "KALSHI-1" in kalshi

    def test_respects_limit(self):
        idx = OpportunityIndex()
        opps = [
            {"_token_ids": [f"poly_{i}"], "net_profit": 0.01 * i}
            for i in range(10)
        ]
        idx.rebuild(opps)

        poly, kalshi = idx.get_subscription_tokens(limit=3)
        assert len(poly) <= 3

    def test_prioritizes_higher_profit(self):
        idx = OpportunityIndex()
        opps = [
            {"_token_ids": ["low_profit"], "net_profit": 0.001},
            {"_token_ids": ["high_profit"], "net_profit": 0.10},
        ]
        idx.rebuild(opps)

        poly, _ = idx.get_subscription_tokens(limit=1)
        assert "high_profit" in poly

    def test_empty_index_returns_empty(self):
        idx = OpportunityIndex()
        idx.rebuild([])

        poly, kalshi = idx.get_subscription_tokens()
        assert poly == []
        assert kalshi == []


# ---------------------------------------------------------------------------
# OpportunityIndex._extract_keys
# ---------------------------------------------------------------------------


class TestExtractKeys:
    def test_extracts_token_ids(self):
        opp = {"_token_ids": ["a", "b"]}
        keys = OpportunityIndex._extract_keys(opp)
        assert ("polymarket", "a") in keys
        assert ("polymarket", "b") in keys

    def test_extracts_kalshi_ticker(self):
        opp = {"_kalshi_ticker": "TICK-1"}
        keys = OpportunityIndex._extract_keys(opp)
        assert ("kalshi", "TICK-1") in keys

    def test_extracts_kalshi_tickers_list(self):
        opp = {"_kalshi_tickers": ["T1", "T2"]}
        keys = OpportunityIndex._extract_keys(opp)
        assert ("kalshi", "T1") in keys
        assert ("kalshi", "T2") in keys

    def test_skips_empty_token_ids(self):
        opp = {"_token_ids": ["valid", "", None]}
        keys = OpportunityIndex._extract_keys(opp)
        assert len([k for k in keys if k[0] == "polymarket"]) == 1

    def test_skips_empty_kalshi_ticker(self):
        opp = {"_kalshi_ticker": ""}
        keys = OpportunityIndex._extract_keys(opp)
        assert len(keys) == 0

    def test_combined_poly_and_kalshi(self):
        opp = {
            "_token_ids": ["poly_tok"],
            "_kalshi_ticker": "KALSHI_TICK",
        }
        keys = OpportunityIndex._extract_keys(opp)
        assert ("polymarket", "poly_tok") in keys
        assert ("kalshi", "KALSHI_TICK") in keys

    def test_no_keys_in_opp(self):
        opp = {"net_profit": 0.05, "market": "test"}
        keys = OpportunityIndex._extract_keys(opp)
        assert keys == []

    def test_extracts_betfair_market_id(self):
        opp = {"type": "BetfairBackAll", "_bf_market_id": "1.234567"}
        keys = OpportunityIndex._extract_keys(opp)
        assert ("betfair", "1.234567") in keys

    def test_extracts_betfair_fallback_market_id(self):
        opp = {"type": "BetfairBackLay", "_market_id": "1.999"}
        keys = OpportunityIndex._extract_keys(opp)
        assert ("betfair", "1.999") in keys

    def test_betfair_key_requires_betfair_type(self):
        """Non-betfair type with _market_id should NOT produce betfair key."""
        opp = {"type": "Binary", "_market_id": "1.999"}
        keys = OpportunityIndex._extract_keys(opp)
        assert ("betfair", "1.999") not in keys

    def test_extracts_smarkets_market_id(self):
        opp = {"type": "SmarketsBackAll", "_sm_market_id": "sm_123"}
        keys = OpportunityIndex._extract_keys(opp)
        assert ("smarkets", "sm_123") in keys

    def test_extracts_sxbet_market_hash(self):
        opp = {"type": "SXBetBackAll", "_sx_market_hash": "0xabc"}
        keys = OpportunityIndex._extract_keys(opp)
        assert ("sxbet", "0xabc") in keys

    def test_extracts_matchbook_market_id(self):
        opp = {"type": "MatchbookBackAll", "_mb_market_id": "mb_456"}
        keys = OpportunityIndex._extract_keys(opp)
        assert ("matchbook", "mb_456") in keys

    def test_extracts_event_divergence_metaculus_key(self):
        opp = {
            "type": "EventDivergence",
            "_platform": "polymarket",
            "_metaculus_id": 12345,
        }
        keys = OpportunityIndex._extract_keys(opp)
        assert ("polymarket", "metaculus_12345") in keys

    def test_event_divergence_no_key_without_platform(self):
        opp = {
            "type": "EventDivergence",
            "_platform": "",
            "_metaculus_id": 12345,
        }
        keys = OpportunityIndex._extract_keys(opp)
        assert not any(k[1].startswith("metaculus_") for k in keys)

    def test_extracts_triangular_cross_keys(self):
        opp = {
            "type": "TriangularCross",
            "market": "Test Market",
            "_platform_a": "polymarket",
            "_platform_b": "kalshi",
        }
        keys = OpportunityIndex._extract_keys(opp)
        assert ("polymarket", "Test Market") in keys
        assert ("kalshi", "Test Market") in keys

    def test_triangular_cross_skips_empty_platforms(self):
        opp = {
            "type": "TriangularCross",
            "market": "Test",
            "_platform_a": "polymarket",
            "_platform_b": "",
        }
        keys = OpportunityIndex._extract_keys(opp)
        assert ("polymarket", "Test") in keys
        assert not any(k[0] == "" for k in keys)


# ---------------------------------------------------------------------------
# _recalc_profit — WS trigger profit recalculation
# ---------------------------------------------------------------------------

class TestRecalcProfit:
    def test_recalc_binary_with_both_cached(self):
        """Should recalculate binary profit when both tokens are in cache."""
        opp = {
            "type": "Binary",
            "_token_ids": ["tok_yes", "tok_no"],
        }
        cache = {
            ("polymarket", "tok_yes"): {"price": 0.40, "_ts": time.time()},
            ("polymarket", "tok_no"): {"price": 0.42, "_ts": time.time()},
        }
        result = _recalc_profit(opp, "polymarket", "tok_yes", 0.40, cache)
        assert result is not None
        # gross = 1.0 - 0.82 = 0.18, should be positive
        assert result > 0

    def test_recalc_binary_missing_other_token(self):
        """Should return None when other token not in cache."""
        opp = {
            "type": "Binary",
            "_token_ids": ["tok_yes", "tok_no"],
        }
        cache = {
            ("polymarket", "tok_yes"): {"price": 0.40, "_ts": time.time()},
        }
        result = _recalc_profit(opp, "polymarket", "tok_yes", 0.40, cache)
        assert result is None

    def test_recalc_negrisk(self):
        """Should recalculate negrisk profit using cached prices."""
        opp = {
            "type": "NegRisk (3 outcomes)",
            "_token_ids": ["t0", "t1", "t2"],
        }
        cache = {
            ("polymarket", "t0"): {"price": 0.20, "_ts": time.time()},
            ("polymarket", "t1"): {"price": 0.25, "_ts": time.time()},
            ("polymarket", "t2"): {"price": 0.30, "_ts": time.time()},
        }
        result = _recalc_profit(opp, "polymarket", "t0", 0.20, cache)
        assert result is not None
        # total = 0.75, gross = 0.25
        assert result > 0

    def test_recalc_unknown_type_returns_none(self):
        """Unknown opportunity types should return None."""
        opp = {"type": "UnknownType"}
        result = _recalc_profit(opp, "polymarket", "tok", 0.5, {})
        assert result is None

    def test_recalc_binary_no_token_ids(self):
        """Binary with missing token_ids should return None."""
        opp = {"type": "Binary", "_token_ids": []}
        result = _recalc_profit(opp, "polymarket", "tok", 0.5, {})
        assert result is None


# ---------------------------------------------------------------------------
# Per-market locking
# ---------------------------------------------------------------------------

class TestPerMarketLocking:
    def test_get_market_lock_creates_new(self):
        """Should create a new lock for an unseen market."""
        lock = _get_market_lock("test-market-unique-12345")
        assert hasattr(lock, "acquire") and hasattr(lock, "release")

    def test_get_market_lock_returns_same(self):
        """Should return the same lock for the same market."""
        lock1 = _get_market_lock("test-market-same-lock-12345")
        lock2 = _get_market_lock("test-market-same-lock-12345")
        assert lock1 is lock2

    def test_different_markets_get_different_locks(self):
        """Different markets should get different locks."""
        lock_a = _get_market_lock("market-a-unique-12345")
        lock_b = _get_market_lock("market-b-unique-12345")
        assert lock_a is not lock_b

    def test_concurrent_locks_allow_parallel_execution(self):
        """Per-market locks should allow concurrent execution on different markets."""
        lock_a = _get_market_lock("concurrent-test-a-12345")
        lock_b = _get_market_lock("concurrent-test-b-12345")

        # Both should be acquirable simultaneously
        assert lock_a.acquire(blocking=False)
        assert lock_b.acquire(blocking=False)
        lock_a.release()
        lock_b.release()

    def test_same_market_lock_blocks(self):
        """Same market lock should block concurrent execution."""
        lock = _get_market_lock("blocking-test-12345")
        assert lock.acquire(blocking=False)
        # Second acquire should fail (non-blocking)
        assert not lock.acquire(blocking=False)
        lock.release()


# ---------------------------------------------------------------------------
# Settlement P&L calculation
# ---------------------------------------------------------------------------

class TestCalcRealizedPnl:
    @pytest.fixture
    def db(self):
        trade_db = TradeDB(":memory:")
        yield trade_db
        trade_db.close()

    def test_uses_fill_prices_when_available(self, db):
        """Should use actual fill prices instead of expected P&L."""
        opp_id = db.log_opportunity("Binary", "M", "", 0.85, 0.15, 0.176, 50, "traded")
        db.log_trade(opp_id, "polymarket", "BUY", 0.40, 5.0, "filled", fill_price=0.41)
        db.log_trade(opp_id, "polymarket", "BUY", 0.45, 5.0, "filled", fill_price=0.46)
        pos_id = db.create_position(opp_id, "m1", "polymarket", 0.15)

        pos = db.get_open_positions()[0]
        realized = _calc_realized_pnl(db, pos)
        # 1.0 - (0.41*5 + 0.46*5) = 1.0 - 4.35 = -3.35
        # For $1 unit: 1.0 - (0.41 + 0.46) = 0.13
        expected = 1.0 - (0.41 * 5.0 + 0.46 * 5.0)
        assert realized == pytest.approx(expected)

    def test_falls_back_to_expected_when_no_trades(self, db):
        """Should fall back to expected_pnl when no trades exist."""
        opp_id = db.log_opportunity("Binary", "M", "", 0.85, 0.15, 0.176, 50, "traded")
        pos_id = db.create_position(opp_id, "m1", "polymarket", 0.15)
        pos = db.get_open_positions()[0]
        realized = _calc_realized_pnl(db, pos)
        assert realized == pytest.approx(0.15)

    def test_uses_order_price_when_no_fill(self, db):
        """Should use order price when fill_price is None."""
        opp_id = db.log_opportunity("Binary", "M", "", 0.85, 0.15, 0.176, 50, "traded")
        db.log_trade(opp_id, "polymarket", "BUY", 0.40, 5.0, "filled")
        db.log_trade(opp_id, "polymarket", "BUY", 0.45, 5.0, "filled")
        pos_id = db.create_position(opp_id, "m1", "polymarket", 0.15)
        pos = db.get_open_positions()[0]
        realized = _calc_realized_pnl(db, pos)
        # No fill prices, uses order prices: 1.0 - (0.40*5 + 0.45*5)
        expected = 1.0 - (0.40 * 5.0 + 0.45 * 5.0)
        assert realized == pytest.approx(expected)
