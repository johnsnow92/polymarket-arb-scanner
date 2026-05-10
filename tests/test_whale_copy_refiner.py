"""Tests for the first-class Stage 2 refiner in scans/whale_copy.py.

Focused on the new live-CLOB behaviour added in PR D:
- Latency gate (existing)
- Decoded-fields gate (drops fillOrders/matchOrders aggregates and
  opps missing _whale_token_id / _whale_side / _whale_price)
- Live CLOB ask/bid via injected fetch_order_book (no network)
- Layer 4 price-move floor (default 10%)
- Liquidity / depth gate
- Size cap against WHALE_COPY_MAX_TRADE_SIZE

Stable module reference (``import scans.whale_copy as wc_mod``)
mirrors the pattern from tests/test_time_decay_refiner.py — see that
file's module note for the rationale on cross-test sys.modules
isolation.
"""

import os
import sys
import time
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock external API modules before importing under test, then restore
# (mirrors the pattern from tests/test_whale_copy.py so polymarket_api
# isn't poisoned for later test files).
_saved_polymarket_api = sys.modules.get("polymarket_api")
_saved_polygonscan_api = sys.modules.get("polygonscan_api")
sys.modules["polymarket_api"] = MagicMock()
sys.modules["polygonscan_api"] = MagicMock()

import scans.whale_copy as wc_mod

# Restore originals so other test files see real modules.
if _saved_polymarket_api is not None:
    sys.modules["polymarket_api"] = _saved_polymarket_api
else:
    sys.modules.pop("polymarket_api", None)
if _saved_polygonscan_api is not None:
    sys.modules["polygonscan_api"] = _saved_polygonscan_api
else:
    sys.modules.pop("polygonscan_api", None)


_refine = wc_mod._refine_whale_copy_with_prices


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_opp(
    *,
    token_id: str = "12345",
    side: str = "BUY",
    price: float = 0.40,
    size: float = 100.0,
    timestamp: int | None = None,
    method: str = "fillOrder",
    address: str = "0x" + "ee" * 20,
):
    if timestamp is None:
        timestamp = int(time.time())
    return {
        "type": "WhaleCopy",
        "_whale_address": address,
        "_whale_tx_hash": "0xabc123",
        "_whale_timestamp": timestamp,
        "_whale_block": 100,
        "_market_key": token_id,
        "_layer": 4,
        "_whale_method": method,
        "_whale_token_id": token_id,
        "_whale_side": side,
        "_whale_price": price,
        "_whale_size": size,
        "_whale_role": "taker",
    }


def _make_book(*, ask: float | None = 0.41, bid: float | None = 0.39,
               ask_size: float = 200.0, bid_size: float = 200.0):
    return {
        "best_ask": ask,
        "best_bid": bid,
        "best_ask_size": ask_size,
        "best_bid_size": bid_size,
    }


def _fake_fetch(book_by_token: dict):
    """Build a fake fetch_order_book that returns a per-token book."""
    def fetch(token_id):
        return book_by_token.get(token_id)
    return fetch


# Defaults so the legacy gate (no fetcher provided) keeps working in
# tests that just want to exercise the latency check.
_LATENCY_BUDGET = wc_mod.WHALE_COPY_LATENCY_BUDGET_SECONDS


# ---------------------------------------------------------------------------
# TestLatencyGate
# ---------------------------------------------------------------------------


class TestLatencyGate:
    def test_drops_stale_opp(self):
        now = 1_000_000.0
        opp = _make_opp(timestamp=int(now - _LATENCY_BUDGET - 5))
        result = _refine(
            [opp],
            fetch_order_book=lambda _: _make_book(),
            current_time=now,
        )
        assert result == []

    def test_keeps_fresh_opp(self):
        now = 1_000_000.0
        opp = _make_opp(timestamp=int(now - 5))
        result = _refine(
            [opp],
            fetch_order_book=lambda _: _make_book(),
            current_time=now,
            min_liquidity=10.0,
            max_trade_size=15.0,
        )
        assert len(result) == 1


# ---------------------------------------------------------------------------
# TestDecodedFieldsGate
# ---------------------------------------------------------------------------


class TestDecodedFieldsGate:
    def test_drops_missing_token_id(self):
        opp = _make_opp(token_id="")
        result = _refine([opp], fetch_order_book=lambda _: _make_book())
        assert result == []

    def test_drops_invalid_side(self):
        opp = _make_opp()
        opp["_whale_side"] = None
        result = _refine([opp], fetch_order_book=lambda _: _make_book())
        assert result == []

    def test_drops_zero_price(self):
        opp = _make_opp(price=0.0)
        result = _refine([opp], fetch_order_book=lambda _: _make_book())
        assert result == []

    def test_drops_aggregate_methods(self):
        for method in ("fillOrders", "matchOrders"):
            opp = _make_opp(method=method)
            result = _refine([opp], fetch_order_book=lambda _: _make_book())
            assert result == [], f"failed for {method}"


# ---------------------------------------------------------------------------
# TestCLOBFetchAndDepth
# ---------------------------------------------------------------------------


class TestCLOBFetchAndDepth:
    def test_drops_when_fetch_returns_none(self):
        opp = _make_opp()
        result = _refine([opp], fetch_order_book=lambda _: None)
        assert result == []

    def test_drops_when_fetch_raises(self):
        opp = _make_opp()
        def boom(_):
            raise RuntimeError("CLOB down")
        result = _refine([opp], fetch_order_book=boom)
        assert result == []

    def test_drops_buy_when_no_ask(self):
        opp = _make_opp(side="BUY")
        book = _make_book(ask=None, bid=0.39)
        result = _refine([opp], fetch_order_book=lambda _: book)
        assert result == []

    def test_drops_sell_when_no_bid(self):
        opp = _make_opp(side="SELL")
        book = _make_book(ask=0.41, bid=None)
        result = _refine([opp], fetch_order_book=lambda _: book)
        assert result == []

    def test_drops_when_depth_below_min(self):
        opp = _make_opp(side="BUY")
        book = _make_book(ask=0.41, ask_size=2.0)  # below default min 10
        result = _refine(
            [opp], fetch_order_book=lambda _: book, min_liquidity=10.0,
        )
        assert result == []

    def test_keeps_when_depth_meets_min(self):
        opp = _make_opp(side="BUY")
        book = _make_book(ask=0.41, ask_size=50.0)
        result = _refine(
            [opp], fetch_order_book=lambda _: book,
            min_liquidity=10.0, max_trade_size=15.0,
        )
        assert len(result) == 1
        assert result[0]["_clob_depth"] == 50.0


# ---------------------------------------------------------------------------
# TestPriceMoveFloor
# ---------------------------------------------------------------------------


class TestPriceMoveFloor:
    def test_drops_buy_when_ask_moved_up_past_floor(self):
        # Whale bought at 0.40; ask is now 0.50 = 25% move > 10% floor.
        opp = _make_opp(side="BUY", price=0.40)
        book = _make_book(ask=0.50, ask_size=200.0)
        result = _refine(
            [opp], fetch_order_book=lambda _: book,
            layer_floor=0.10, min_liquidity=10.0, max_trade_size=15.0,
        )
        assert result == []

    def test_keeps_buy_when_ask_moved_up_within_floor(self):
        # 5% move < 10% floor → keep.
        opp = _make_opp(side="BUY", price=0.40)
        book = _make_book(ask=0.42, ask_size=200.0)
        result = _refine(
            [opp], fetch_order_book=lambda _: book,
            layer_floor=0.10, min_liquidity=10.0, max_trade_size=15.0,
        )
        assert len(result) == 1
        assert result[0]["_price_move"] > 0  # moved against us, but kept

    def test_keeps_buy_when_ask_moved_down(self):
        # Ask dropped — favourable for a BUY copy. Must NOT drop.
        opp = _make_opp(side="BUY", price=0.40)
        book = _make_book(ask=0.30, ask_size=200.0)
        result = _refine(
            [opp], fetch_order_book=lambda _: book,
            layer_floor=0.10, min_liquidity=10.0, max_trade_size=15.0,
        )
        assert len(result) == 1
        assert result[0]["_price_move"] < 0  # moved in our favour

    def test_drops_sell_when_bid_moved_down_past_floor(self):
        # Whale sold at 0.60; bid is now 0.40 = 33% move > 10% floor.
        opp = _make_opp(side="SELL", price=0.60)
        book = _make_book(ask=0.50, bid=0.40, bid_size=200.0)
        result = _refine(
            [opp], fetch_order_book=lambda _: book,
            layer_floor=0.10, min_liquidity=10.0, max_trade_size=15.0,
        )
        assert result == []

    def test_keeps_sell_when_bid_moved_up(self):
        # Bid rose — favourable for SELL copy.
        opp = _make_opp(side="SELL", price=0.60)
        book = _make_book(ask=0.70, bid=0.65, bid_size=200.0)
        result = _refine(
            [opp], fetch_order_book=lambda _: book,
            layer_floor=0.10, min_liquidity=10.0, max_trade_size=15.0,
        )
        assert len(result) == 1


# ---------------------------------------------------------------------------
# TestSizeCap
# ---------------------------------------------------------------------------


class TestSizeCap:
    def test_caps_dollar_size_at_max_trade_size(self):
        # Whale bought 100 tokens at 0.41 = $41 worth. Cap is $15.
        opp = _make_opp(side="BUY", price=0.40, size=100.0)
        book = _make_book(ask=0.41, ask_size=500.0)
        result = _refine(
            [opp], fetch_order_book=lambda _: book,
            max_trade_size=15.0, layer_floor=0.10, min_liquidity=10.0,
        )
        assert len(result) == 1
        assert result[0]["_copy_size_capped"] == 15.0
        assert result[0]["_whale_size"] == 100.0  # original preserved

    def test_does_not_inflate_below_cap(self):
        # Whale only bought 5 tokens at 0.41 = $2.05 — below $15 cap.
        opp = _make_opp(side="BUY", price=0.40, size=5.0)
        book = _make_book(ask=0.41, ask_size=500.0)
        result = _refine(
            [opp], fetch_order_book=lambda _: book,
            max_trade_size=15.0, layer_floor=0.10, min_liquidity=10.0,
        )
        assert len(result) == 1
        # 5 tokens * 0.41 = 2.05
        assert abs(result[0]["_copy_size_capped"] - 2.05) < 1e-6


# ---------------------------------------------------------------------------
# TestNormalisedFieldsOnSurvivors
# ---------------------------------------------------------------------------


class TestNormalisedFieldsOnSurvivors:
    def test_writes_live_quote_fields(self):
        opp = _make_opp(side="BUY", price=0.40, size=20.0)
        book = _make_book(ask=0.42, bid=0.41, ask_size=300.0, bid_size=250.0)
        result = _refine(
            [opp], fetch_order_book=lambda _: book,
            max_trade_size=15.0, layer_floor=0.10, min_liquidity=10.0,
        )
        assert len(result) == 1
        r = result[0]
        assert r["_current_ask"] == 0.42
        assert r["_current_bid"] == 0.41
        assert r["_market_price"] == 0.42  # BUY uses ask as our-side price
        assert r["_clob_depth"] == 300.0
        assert "_price_move" in r


class TestEmptyInput:
    def test_empty_returns_empty(self):
        assert _refine([]) == []
