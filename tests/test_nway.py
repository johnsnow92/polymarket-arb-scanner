"""Tests for scans/triangular.py scan_nway_arb — Strategy #32 N-Way Arbitrage."""

import pytest
from unittest.mock import patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock py_clob_client before importing scan modules
sys.modules["py_clob_client"] = MagicMock()
sys.modules["py_clob_client.clob_types"] = MagicMock()
sys.modules["py_clob_client.client"] = MagicMock()

from scans.triangular import scan_nway_arb


class TestScanNwayArb:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        with patch("config.NWAY_ARB_ENABLED", True):
            yield

    def test_disabled_returns_empty(self):
        with patch("config.NWAY_ARB_ENABLED", False):
            result = scan_nway_arb([])
            assert result == []

    def test_empty_matches_returns_empty(self):
        result = scan_nway_arb([])
        assert result == []

    def test_fewer_than_min_platforms_returns_empty(self):
        matches = [
            {
                "platform_a": "polymarket",
                "platform_b": "kalshi",
                "market_a": {"yes_price": 0.40},
                "market_b": {"yes_price": 0.45},
            }
        ]
        result = scan_nway_arb(matches, min_platforms=4)
        assert result == []

    def test_opportunity_structure(self):
        matches = [
            {
                "platform_a": "polymarket",
                "platform_b": "kalshi",
                "market_a": {"yes_price": 0.30, "condition_id": "m1", "title": "Test"},
                "market_b": {"yes_price": 0.35, "ticker": "TEST"},
                "title": "Test Market",
            },
            {
                "platform_a": "kalshi",
                "platform_b": "betfair",
                "market_a": {"yes_price": 0.35, "ticker": "TEST"},
                "market_b": {"yes_price": 0.25, "market_id": "bf1"},
                "title": "Test Market",
            },
            {
                "platform_a": "betfair",
                "platform_b": "smarkets",
                "market_a": {"yes_price": 0.25, "market_id": "bf1"},
                "market_b": {"yes_price": 0.20, "contract_id": "sm1"},
                "title": "Test Market",
            },
            {
                "platform_a": "polymarket",
                "platform_b": "smarkets",
                "market_a": {"yes_price": 0.30, "condition_id": "m1", "title": "Test"},
                "market_b": {"yes_price": 0.20, "contract_id": "sm1"},
                "title": "Test Market",
            },
        ]
        result = scan_nway_arb(matches, min_platforms=4, min_profit=0.001)

        for opp in result:
            assert opp["type"] == "NWayArb"
            assert opp["_layer"] == 1
            assert "net_profit" in opp
            assert "_platforms" in opp
            assert len(opp["_platforms"]) >= 4


class TestNwayFeeFunction:
    def test_net_profit_nway_basic(self):
        from fees import net_profit_nway
        platform_prices = [
            ("polymarket", 0.30),
            ("kalshi", 0.25),
            ("betfair", 0.20),
            ("smarkets", 0.15),
        ]
        result = net_profit_nway(platform_prices)
        assert "net_profit" in result
        assert "gross_spread" in result
        assert "total_cost" in result

    def test_net_profit_nway_sum_above_one(self):
        from fees import net_profit_nway
        platform_prices = [
            ("polymarket", 0.40),
            ("kalshi", 0.40),
            ("betfair", 0.40),
        ]
        result = net_profit_nway(platform_prices)
        # Sum = 1.20, no profit
        assert result["gross_spread"] < 0

    def test_net_profit_nway_sum_below_one(self):
        from fees import net_profit_nway
        platform_prices = [
            ("polymarket", 0.20),
            ("kalshi", 0.20),
            ("betfair", 0.20),
            ("smarkets", 0.20),
        ]
        result = net_profit_nway(platform_prices)
        # Sum = 0.80, spread = 0.20
        assert result["gross_spread"] == pytest.approx(0.20, abs=0.001)

    def test_net_profit_nway_includes_gemini_fee(self):
        from fees import net_profit_nway
        platform_prices = [
            ("gemini", 0.30),
            ("kalshi", 0.30),
        ]
        result = net_profit_nway(platform_prices)
        assert result["fees"] > 0
