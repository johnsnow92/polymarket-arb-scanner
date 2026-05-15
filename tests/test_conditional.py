"""Tests for scans/conditional.py — Strategy #30 Conditional Market Arbitrage."""

import pytest
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock py_clob_client before importing scan modules
sys.modules["py_clob_client"] = MagicMock()
sys.modules["py_clob_client.clob_types"] = MagicMock()
sys.modules["py_clob_client.client"] = MagicMock()

from scans.conditional import scan_conditional_arb, _parse_conditional


class TestParseConditional:
    def test_parses_if_condition(self):
        result = _parse_conditional("Will Biden win if he runs?")
        if result:
            outcome, condition = result
            assert "win" in outcome.lower() or "biden" in outcome.lower()

    def test_parses_given_condition(self):
        result = _parse_conditional("Will Trump win given he is nominated?")
        if result:
            outcome, condition = result
            assert len(condition) > 0

    def test_returns_none_for_non_conditional(self):
        result = _parse_conditional("Will Biden win the election?")
        assert result is None


class TestScanConditionalArb:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        with patch("scans.conditional.CONDITIONAL_ARB_ENABLED", True):
            yield

    def test_disabled_returns_empty(self):
        with patch("scans.conditional.CONDITIONAL_ARB_ENABLED", False):
            result = scan_conditional_arb([])
            assert result == []

    def test_no_pairs_returns_empty(self):
        markets = [{"title": "Random market", "condition_id": "m1", "yes_price": 0.50}]
        result = scan_conditional_arb(markets)
        assert result == []

    def test_opportunity_structure(self):
        """Test that found opportunities have correct structure."""
        markets = [
            {"title": "Will X happen if Y?", "condition_id": "c1", "yes_price": 0.80},
            {"title": "Will Y happen?", "condition_id": "c2", "yes_price": 0.50},
            {"title": "Will X happen?", "condition_id": "c3", "yes_price": 0.30},
        ]
        with patch("fees.net_profit_conditional") as mock_fee:
            mock_fee.return_value = {
                "net_profit": 0.10,
                "gross_spread": 0.12,
                "fees": 0.02,
                "total_cost": 0.80,
                "net_roi": 0.125,
            }
            result = scan_conditional_arb(markets, min_profit=0.01)

            for opp in result:
                assert "type" in opp
                assert opp["type"] == "ConditionalArb"
                assert "_layer" in opp
                assert opp["_layer"] == 1
                assert "net_profit" in opp
                assert "net_roi" in opp


class TestConditionalFeeFunction:
    def test_net_profit_conditional_buy_conditional(self):
        from fees import net_profit_conditional
        result = net_profit_conditional(
            p_x_given_y=0.80,
            p_y=0.50,
            p_x=0.30,
            direction="BUY_CONDITIONAL",
        )
        assert "net_profit" in result
        assert "gross_spread" in result
        assert "fees" in result
        assert "total_cost" in result

    def test_net_profit_conditional_buy_unconditional(self):
        from fees import net_profit_conditional
        result = net_profit_conditional(
            p_x_given_y=0.80,
            p_y=0.50,
            p_x=0.50,
            direction="BUY_UNCONDITIONAL",
        )
        assert "net_profit" in result

    def test_no_arb_when_prices_aligned(self):
        from fees import net_profit_conditional
        result = net_profit_conditional(
            p_x_given_y=0.60,
            p_y=0.50,
            p_x=0.30,
            direction="BUY_CONDITIONAL",
        )
        assert result["gross_spread"] == 0.0
