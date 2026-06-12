"""Tests for the exit-liquidity entry-discipline gate.

Production evidence (June 2026): the bot entered markets whose books were
one-sided — partial fills could not be hedged because there were no resting
bids to exit into (97 of 104 hedge attempts failed). MIN_ENTRY_PRICE blocks
penny longshots; this gate blocks one-sided books at ANY price by checking
the live order book before order placement. Fails closed on fetch errors.
Live executions only — dry-run creates no positions to hedge.

Patching note: other test files delete/reimport ``executor`` from
sys.modules, so ``patch("executor.<name>")`` can target a different module
object than the one ArbitrageExecutor was defined in. All patches here go
through the class's own ``__globals__`` to stay pollution-proof.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# test_cli.py imports cli (→ executor) under sys.modules stubs at collection
# time, leaving a polluted executor module cached with MagicMock helpers.
# Re-import fresh so the gate's globals hold the real polymarket_api helpers
# (same dance as test_concurrent_execution.py).
if "executor" in sys.modules:
    del sys.modules["executor"]
from executor import ArbitrageExecutor

# The namespace the gate's name lookups actually resolve against.
_GATE_GLOBALS = ArbitrageExecutor._check_exit_liquidity.__globals__


def _patch_gate_global(name, value):
    return patch.dict(_GATE_GLOBALS, {name: value})


def _make_executor(kalshi_client=None):
    """Bare executor instance without running __init__ (no file handles, no deps)."""
    ex = ArbitrageExecutor.__new__(ArbitrageExecutor)
    ex.kalshi_client = kalshi_client
    ex.dry_run = False
    return ex


def _kalshi_book(yes_bids=None, no_bids=None):
    """Kalshi-shaped orderbook (current 2026 API shape; bids only, ascending)."""
    return {
        "orderbook_fp": {
            "yes_dollars": yes_bids or [],
            "no_dollars": no_bids or [],
        }
    }


class TestExitLiquidityGateKalshi(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.ex = _make_executor(kalshi_client=self.client)

    def test_two_sided_book_passes(self):
        # Best YES bid $0.38 x 50, best NO bid $0.58 x 40 — healthy book.
        self.client.fetch_order_book.return_value = _kalshi_book(
            yes_bids=[["0.10", "10.00"], ["0.38", "50.00"]],
            no_bids=[["0.20", "15.00"], ["0.58", "40.00"]],
        )
        opp = {"type": "KalshiBinary"}
        legs = [
            {"platform": "kalshi", "side": "yes", "action": "buy", "price": 0.40, "_ticker": "K-T"},
            {"platform": "kalshi", "side": "no", "action": "buy", "price": 0.58, "_ticker": "K-T"},
        ]
        ok, reason = self.ex._check_exit_liquidity(opp, legs)
        self.assertTrue(ok, reason)

    def test_no_yes_bids_blocked(self):
        # Buying YES with zero resting YES bids — the production failure mode.
        self.client.fetch_order_book.return_value = _kalshi_book(
            yes_bids=[],
            no_bids=[["0.90", "100.00"]],
        )
        opp = {"type": "KalshiMulti(3)"}
        legs = [{"platform": "kalshi", "side": "yes", "action": "buy", "price": 0.08, "_ticker": "K-LONGSHOT"}]
        ok, reason = self.ex._check_exit_liquidity(opp, legs)
        self.assertFalse(ok)
        self.assertIn("one-sided", reason)

    def test_thin_exit_depth_blocked(self):
        # Bid exists but only 2 contracts deep — below MIN_EXIT_BID_DEPTH (10).
        self.client.fetch_order_book.return_value = _kalshi_book(
            yes_bids=[["0.35", "2.00"]],
            no_bids=[["0.60", "100.00"]],
        )
        opp = {"type": "KalshiBinary"}
        legs = [{"platform": "kalshi", "side": "yes", "action": "buy", "price": 0.40, "_ticker": "K-THIN"}]
        with _patch_gate_global("MIN_EXIT_BID_DEPTH", 10):
            ok, reason = self.ex._check_exit_liquidity(opp, legs)
        self.assertFalse(ok)
        self.assertIn("MIN_EXIT_BID_DEPTH", reason)

    def test_fetch_failure_fails_closed(self):
        self.client.fetch_order_book.return_value = None
        opp = {"type": "KalshiBinary"}
        legs = [{"platform": "kalshi", "side": "yes", "action": "buy", "price": 0.40, "_ticker": "K-ERR"}]
        ok, reason = self.ex._check_exit_liquidity(opp, legs)
        self.assertFalse(ok)
        self.assertIn("fail closed", reason)

    def test_book_fetched_once_per_ticker(self):
        # Binary arb has two legs on the same ticker — one fetch, not two.
        self.client.fetch_order_book.return_value = _kalshi_book(
            yes_bids=[["0.38", "50.00"]],
            no_bids=[["0.58", "40.00"]],
        )
        opp = {"type": "KalshiBinary"}
        legs = [
            {"platform": "kalshi", "side": "yes", "action": "buy", "price": 0.40, "_ticker": "K-T"},
            {"platform": "kalshi", "side": "no", "action": "buy", "price": 0.58, "_ticker": "K-T"},
        ]
        self.ex._check_exit_liquidity(opp, legs)
        self.assertEqual(self.client.fetch_order_book.call_count, 1)

    def test_sell_legs_not_gated(self):
        opp = {"type": "Spread"}
        legs = [{"platform": "kalshi", "side": "yes", "action": "sell", "price": 0.40, "_ticker": "K-T"}]
        ok, _ = self.ex._check_exit_liquidity(opp, legs)
        self.assertTrue(ok)
        self.assertEqual(self.client.fetch_order_book.call_count, 0)


class TestExitLiquidityGatePolymarket(unittest.TestCase):
    def setUp(self):
        self.ex = _make_executor()

    def test_healthy_bid_passes(self):
        book = {"bids": [{"price": "0.40", "size": "50"}], "asks": [{"price": "0.45", "size": "30"}]}
        opp = {"type": "Binary"}
        legs = [{"platform": "polymarket", "side": "BUY", "token": "yes", "price": 0.45, "_token_id": "tok-1"}]
        with _patch_gate_global("fetch_order_book", MagicMock(return_value=book)):
            ok, reason = self.ex._check_exit_liquidity(opp, legs)
        self.assertTrue(ok, reason)

    def test_empty_bids_blocked(self):
        book = {"bids": [], "asks": [{"price": "0.05", "size": "500"}]}
        opp = {"type": "NegRiskNO(4)"}
        legs = [{"platform": "polymarket", "side": "BUY", "token": "no_0", "price": 0.10, "_token_id": "tok-2"}]
        with _patch_gate_global("fetch_order_book", MagicMock(return_value=book)):
            ok, reason = self.ex._check_exit_liquidity(opp, legs)
        self.assertFalse(ok)
        self.assertIn("one-sided", reason)

    def test_thin_bid_depth_blocked(self):
        book = {"bids": [{"price": "0.30", "size": "3"}], "asks": [{"price": "0.35", "size": "30"}]}
        opp = {"type": "Binary"}
        legs = [{"platform": "polymarket", "side": "BUY", "token": "yes", "price": 0.35, "_token_id": "tok-3"}]
        with _patch_gate_global("fetch_order_book", MagicMock(return_value=book)), \
             _patch_gate_global("MIN_EXIT_BID_DEPTH", 10):
            ok, reason = self.ex._check_exit_liquidity(opp, legs)
        self.assertFalse(ok)
        self.assertIn("MIN_EXIT_BID_DEPTH", reason)

    def test_fetch_failure_fails_closed(self):
        opp = {"type": "Binary"}
        legs = [{"platform": "polymarket", "side": "BUY", "token": "yes", "price": 0.45, "_token_id": "tok-4"}]
        with _patch_gate_global("fetch_order_book", MagicMock(return_value=None)):
            ok, reason = self.ex._check_exit_liquidity(opp, legs)
        self.assertFalse(ok)
        self.assertIn("fail closed", reason)


class TestExitLiquidityGateExemptions(unittest.TestCase):
    def setUp(self):
        self.ex = _make_executor()

    def test_gate_disabled_passes_everything(self):
        opp = {"type": "KalshiBinary"}
        legs = [{"platform": "kalshi", "side": "yes", "action": "buy", "price": 0.01, "_ticker": "K-X"}]
        with _patch_gate_global("EXIT_LIQUIDITY_GATE_ENABLED", False):
            ok, _ = self.ex._check_exit_liquidity(opp, legs)
        self.assertTrue(ok)

    def test_dry_run_skips_gate(self):
        ex = _make_executor(kalshi_client=MagicMock())
        ex.dry_run = True
        opp = {"type": "KalshiBinary"}
        legs = [{"platform": "kalshi", "side": "yes", "action": "buy", "price": 0.40, "_ticker": "K-T"}]
        ok, _ = ex._check_exit_liquidity(opp, legs)
        self.assertTrue(ok)
        ex.kalshi_client.fetch_order_book.assert_not_called()

    def test_market_make_exempt(self):
        opp = {"type": "MarketMake"}
        legs = [{"platform": "kalshi", "side": "yes", "action": "buy", "price": 0.02, "_ticker": "K-MM"}]
        ok, _ = self.ex._check_exit_liquidity(opp, legs)
        self.assertTrue(ok)

    def test_unwired_platform_skipped(self):
        # Betfair legs price in decimal odds / have no wired book check — skip.
        opp = {"type": "BetfairBackAll"}
        legs = [{"platform": "betfair", "side": "BACK", "action": "buy", "price": 0.45}]
        ok, _ = self.ex._check_exit_liquidity(opp, legs)
        self.assertTrue(ok)

    def test_missing_kalshi_client_skips_kalshi_legs(self):
        # No client wired — cannot check, and execution would fail later anyway.
        self.ex.kalshi_client = None
        opp = {"type": "KalshiBinary"}
        legs = [{"platform": "kalshi", "side": "yes", "action": "buy", "price": 0.40, "_ticker": "K-T"}]
        ok, _ = self.ex._check_exit_liquidity(opp, legs)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
