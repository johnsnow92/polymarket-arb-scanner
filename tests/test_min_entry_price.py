"""Tests for the MIN_ENTRY_PRICE entry-discipline gate.

Production evidence (June 2026): the bot filled $0.01 longshot sports
positions that had no resting bids to exit into — 97 of 104 hedge attempts
failed. The gate refuses taker/arb entries below MIN_ENTRY_PRICE so the
hedger is never asked to save an unexitable position. MarketMake is exempt.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executor import _check_min_entry_price


class TestMinEntryPriceGate(unittest.TestCase):
    def test_blocks_penny_kalshi_buy(self):
        opp = {"type": "KalshiMulti(4)"}
        legs = [{"platform": "kalshi", "side": "yes", "action": "buy", "price": 0.01}]
        ok, reason = _check_min_entry_price(opp, legs)
        self.assertFalse(ok)
        self.assertIn("MIN_ENTRY_PRICE", reason)

    def test_blocks_penny_polymarket_buy(self):
        opp = {"type": "Binary"}
        legs = [
            {"platform": "polymarket", "side": "BUY", "price": 0.45},
            {"platform": "polymarket", "side": "BUY", "price": 0.03},
        ]
        ok, _ = _check_min_entry_price(opp, legs)
        self.assertFalse(ok)

    def test_allows_normal_priced_entry(self):
        opp = {"type": "KalshiBinary"}
        legs = [
            {"platform": "kalshi", "side": "yes", "action": "buy", "price": 0.40},
            {"platform": "kalshi", "side": "no", "action": "buy", "price": 0.55},
        ]
        ok, reason = _check_min_entry_price(opp, legs)
        self.assertTrue(ok, reason)

    def test_exactly_at_threshold_allowed(self):
        opp = {"type": "Binary"}
        legs = [{"platform": "polymarket", "side": "BUY", "price": 0.05}]
        ok, _ = _check_min_entry_price(opp, legs)
        self.assertTrue(ok)

    def test_market_make_exempt(self):
        opp = {"type": "MarketMake"}
        legs = [{"platform": "kalshi", "side": "yes", "action": "buy", "price": 0.01}]
        ok, _ = _check_min_entry_price(opp, legs)
        self.assertTrue(ok)

    def test_sell_legs_not_gated(self):
        opp = {"type": "Spread"}
        legs = [{"platform": "polymarket", "side": "SELL", "price": 0.02}]
        ok, _ = _check_min_entry_price(opp, legs)
        self.assertTrue(ok)

    def test_kalshi_sell_action_not_gated(self):
        opp = {"type": "KalshiBinary"}
        legs = [{"platform": "kalshi", "side": "yes", "action": "sell", "price": 0.02}]
        ok, _ = _check_min_entry_price(opp, legs)
        self.assertTrue(ok)

    def test_decimal_odds_legs_skipped(self):
        # Exchange back/lay legs price in decimal odds (>1) — not gated.
        opp = {"type": "BetfairBackAll"}
        legs = [{"platform": "betfair", "side": "BACK", "price": 21.0}]
        ok, _ = _check_min_entry_price(opp, legs)
        self.assertTrue(ok)

    def test_missing_price_skipped(self):
        opp = {"type": "Binary"}
        legs = [{"platform": "polymarket", "side": "BUY"}]
        ok, _ = _check_min_entry_price(opp, legs)
        self.assertTrue(ok)

    def test_gate_disabled_when_zero(self):
        opp = {"type": "Binary"}
        legs = [{"platform": "polymarket", "side": "BUY", "price": 0.01}]
        # Patch the function's own globals: other test modules reload
        # `executor` via sys.modules, so patch("executor.MIN_ENTRY_PRICE")
        # can hit a different module object than the one this function
        # was imported from.
        with patch.dict(_check_min_entry_price.__globals__, {"MIN_ENTRY_PRICE": 0}):
            ok, _ = _check_min_entry_price(opp, legs)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
