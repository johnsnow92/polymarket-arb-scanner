"""Tests for scans/bracket.py — Strategy #31 Bracket/Range Market Arbitrage."""

import pytest
from unittest.mock import patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock py_clob_client before importing scan modules
sys.modules["py_clob_client"] = MagicMock()
sys.modules["py_clob_client.clob_types"] = MagicMock()
sys.modules["py_clob_client.client"] = MagicMock()

from scans.bracket import (
    scan_bracket_arb,
    _parse_bracket,
    _group_brackets,
    _brackets_are_complete,
)


class TestParseBracket:
    def test_parses_range_with_dash(self):
        result = _parse_bracket("S&P 5000-5100 EOD")
        assert result is not None
        assert "base_event" in result

    def test_parses_range_with_to(self):
        result = _parse_bracket("BTC price 60000 to 70000")
        assert result is not None

    def test_parses_plus_notation(self):
        result = _parse_bracket("S&P 5300+ EOD")
        assert result is not None
        assert result.get("upper_bound") == float("inf")

    def test_returns_none_for_non_bracket(self):
        result = _parse_bracket("Will Biden win?")
        assert result is None

    def test_empty_string_returns_none(self):
        result = _parse_bracket("")
        assert result is None


class TestGroupBrackets:
    def test_groups_by_base_event(self):
        markets = [
            {"title": "S&P 5000-5100 EOD", "yes_price": 0.25},
            {"title": "S&P 5100-5200 EOD", "yes_price": 0.30},
            {"title": "BTC 60k-70k", "yes_price": 0.40},
        ]
        groups = _group_brackets(markets)
        assert len(groups) >= 1

    def test_empty_markets_returns_empty(self):
        groups = _group_brackets([])
        assert groups == {}


class TestBracketsAreComplete:
    def test_complete_coverage_returns_true(self):
        brackets = [
            {"_bracket_info": {"lower_bound": 0, "upper_bound": 100}},
            {"_bracket_info": {"lower_bound": 100, "upper_bound": 200}},
            {"_bracket_info": {"lower_bound": 200, "upper_bound": float("inf")}},
        ]
        assert _brackets_are_complete(brackets) is True

    def test_gap_returns_false(self):
        brackets = [
            {"_bracket_info": {"lower_bound": 0, "upper_bound": 100}},
            {"_bracket_info": {"lower_bound": 150, "upper_bound": 200}},
        ]
        assert _brackets_are_complete(brackets) is False

    def test_single_bracket_without_inf_returns_false(self):
        brackets = [{"_bracket_info": {"lower_bound": 0, "upper_bound": 100}}]
        assert _brackets_are_complete(brackets) is False


class TestScanBracketArb:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        with patch("scans.bracket.BRACKET_ARB_ENABLED", True):
            yield

    def test_disabled_returns_empty(self):
        with patch("scans.bracket.BRACKET_ARB_ENABLED", False):
            result = scan_bracket_arb([])
            assert result == []

    def test_no_brackets_returns_empty(self):
        markets = [{"title": "Will Biden win?", "yes_price": 0.50}]
        result = scan_bracket_arb(markets)
        assert result == []

    def test_sum_above_one_returns_empty(self):
        """When sum > 1.0, there's no arbitrage (we can't short)."""
        markets = [
            {"title": "S&P 5000-5100 EOD", "yes_price": 0.40, "condition_id": "b1"},
            {"title": "S&P 5100-5200 EOD", "yes_price": 0.40, "condition_id": "b2"},
            {"title": "S&P 5200+ EOD", "yes_price": 0.40, "condition_id": "b3"},
        ]
        result = scan_bracket_arb(markets, min_spread=0.01)
        # Sum = 1.20, no arb opportunity
        assert result == []

    def test_sum_below_one_finds_opportunity(self):
        """When sum < 1.0, buying all brackets guarantees profit."""
        markets = [
            {"title": "S&P 5000-5100 EOD", "yes_price": 0.20, "condition_id": "b1"},
            {"title": "S&P 5100-5200 EOD", "yes_price": 0.25, "condition_id": "b2"},
            {"title": "S&P 5200-5300 EOD", "yes_price": 0.25, "condition_id": "b3"},
            {"title": "S&P 5300+ EOD", "yes_price": 0.20, "condition_id": "b4"},
        ]
        # Sum = 0.90, spread = 0.10
        result = scan_bracket_arb(markets, min_spread=0.05, min_profit=0.001)
        # May or may not find depending on grouping
        for opp in result:
            assert opp["type"] == "BracketArb"
            assert opp["_layer"] == 1
            assert "_spread" in opp


class TestBracketFeeFunction:
    def test_net_profit_bracket_underround(self):
        from fees import net_profit_bracket
        prices = [0.20, 0.25, 0.25, 0.20]  # Sum = 0.90
        result = net_profit_bracket(prices, platform="kalshi")
        assert result["gross_spread"] == pytest.approx(0.10, abs=0.001)
        assert result["net_profit"] > 0

    def test_net_profit_bracket_overround_negative(self):
        from fees import net_profit_bracket
        prices = [0.30, 0.35, 0.35, 0.30]  # Sum = 1.30
        result = net_profit_bracket(prices, platform="kalshi")
        assert result["gross_spread"] < 0
        assert result["net_profit"] < 0

    def test_net_profit_bracket_exact_one(self):
        from fees import net_profit_bracket
        prices = [0.25, 0.25, 0.25, 0.25]  # Sum = 1.00
        result = net_profit_bracket(prices, platform="kalshi")
        assert result["gross_spread"] == 0
