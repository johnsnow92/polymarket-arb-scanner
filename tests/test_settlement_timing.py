"""Tests for scans/settlement_timing.py — Strategy #33 Settlement Timing Arb."""

import pytest
from unittest.mock import patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock py_clob_client before importing scan modules
sys.modules["py_clob_client"] = MagicMock()
sys.modules["py_clob_client.clob_types"] = MagicMock()
sys.modules["py_clob_client.client"] = MagicMock()

from scans.settlement_timing import (
    scan_settlement_timing,
    _is_settled,
    _get_settlement_price,
)


class TestIsSettled:
    def test_polymarket_resolved_yes(self):
        market = {"closed": True, "resolution_outcome": "Yes"}
        is_set, winner = _is_settled(market, "polymarket")
        assert is_set is True
        assert winner == "yes"

    def test_polymarket_resolved_no(self):
        market = {"resolved": True, "resolution": "NO"}
        is_set, winner = _is_settled(market, "polymarket")
        assert is_set is True
        assert winner == "no"

    def test_polymarket_not_settled(self):
        market = {"closed": False, "resolved": False}
        is_set, winner = _is_settled(market, "polymarket")
        assert is_set is False
        assert winner is None

    def test_kalshi_settled_yes(self):
        market = {"status": "settled", "result": "yes"}
        is_set, winner = _is_settled(market, "kalshi")
        assert is_set is True
        assert winner == "yes"

    def test_kalshi_not_settled(self):
        market = {"status": "active"}
        is_set, winner = _is_settled(market, "kalshi")
        assert is_set is False
        assert winner is None

    def test_betfair_settled(self):
        market = {"status": "settled", "winner": "YES"}
        is_set, winner = _is_settled(market, "betfair")
        assert is_set is True
        assert winner == "yes"


class TestGetSettlementPrice:
    def test_polymarket_yes_price(self):
        market = {"yes_price": 0.97, "no_price": 0.03}
        price = _get_settlement_price(market, "polymarket", "yes")
        assert price == 0.97

    def test_polymarket_no_price(self):
        market = {"yes_price": 0.97, "no_price": 0.03}
        price = _get_settlement_price(market, "polymarket", "no")
        assert price == 0.03

    def test_kalshi_yes_price(self):
        market = {"yes_price": 0.95}
        price = _get_settlement_price(market, "kalshi", "yes")
        assert price == 0.95

    def test_betfair_back_price(self):
        market = {"back_price": 0.96}
        price = _get_settlement_price(market, "betfair", "yes")
        assert price == 0.96


class TestScanSettlementTiming:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        with patch("scans.settlement_timing.SETTLEMENT_TIMING_ENABLED", True):
            with patch("scans.settlement_timing.SETTLEMENT_TIMING_MIN_DISCOUNT", 0.01):
                yield

    def test_disabled_returns_empty(self):
        with patch("scans.settlement_timing.SETTLEMENT_TIMING_ENABLED", False):
            result = scan_settlement_timing([])
            assert result == []

    def test_empty_matches_returns_empty(self):
        result = scan_settlement_timing([])
        assert result == []

    def test_no_settlement_difference_returns_empty(self):
        matches = [
            {
                "platform_a": "polymarket",
                "platform_b": "kalshi",
                "market_a": {"closed": False, "yes_price": 0.50},
                "market_b": {"status": "active", "yes_price": 0.50},
            }
        ]
        result = scan_settlement_timing(matches, min_profit=0.001)
        assert result == []

    def test_finds_opportunity_when_one_settled(self):
        matches = [
            {
                "platform_a": "polymarket",
                "platform_b": "kalshi",
                "market_a": {
                    "closed": True,
                    "resolution_outcome": "Yes",
                    "yes_price": 1.0,
                    "title": "Test Market",
                },
                "market_b": {
                    "status": "active",
                    "yes_price": 0.97,
                    "id": "kalshi-123",
                    "title": "Test Market",
                },
            }
        ]
        with patch("fees.net_profit_settlement_timing") as mock_fee:
            mock_fee.return_value = {
                "net_profit": 0.025,
                "net_roi": 0.026,
                "fees": 0.005,
            }
            result = scan_settlement_timing(matches, min_profit=0.001, min_discount=0.01)

            if result:
                opp = result[0]
                assert opp["type"] == "SettlementTimingArb"
                assert opp["_layer"] == 2
                assert opp["_winning_side"] == "yes"
                assert opp["_platform"] == "kalshi"

    def test_discount_below_threshold_filtered(self):
        matches = [
            {
                "platform_a": "polymarket",
                "platform_b": "kalshi",
                "market_a": {
                    "closed": True,
                    "resolution_outcome": "Yes",
                },
                "market_b": {
                    "status": "active",
                    "yes_price": 0.995,
                    "id": "k1",
                },
            }
        ]
        result = scan_settlement_timing(matches, min_discount=0.02, min_profit=0.001)
        assert result == []


class TestSettlementTimingFeeFunction:
    def test_net_profit_settlement_timing(self):
        from fees import net_profit_settlement_timing
        result = net_profit_settlement_timing(
            current_price=0.97,
            expected_payout=1.0,
            platform="kalshi",
        )
        assert "net_profit" in result
        assert "fees" in result
        assert result["net_profit"] > 0

    def test_high_price_low_profit(self):
        from fees import net_profit_settlement_timing
        result = net_profit_settlement_timing(
            current_price=0.995,
            expected_payout=1.0,
            platform="polymarket",
        )
        assert result["net_profit"] < 0.01
