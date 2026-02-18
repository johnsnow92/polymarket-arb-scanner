"""Tests for Opinion fee functions and scans/opinion.py scan functions."""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fees import (
    net_profit_opinion_binary,
    net_profit_opinion_multi,
    net_profit_cross_opinion,
    BNB_GAS_ESTIMATE,
)


class TestOpinionBinaryFees:
    def test_profitable_binary(self):
        """YES + NO < $1.00 should yield positive net profit minus gas."""
        result = net_profit_opinion_binary(0.40, 0.50)
        assert result["gross_spread"] == pytest.approx(0.10)
        expected_gas = BNB_GAS_ESTIMATE * 2
        assert result["fees"] == pytest.approx(expected_gas)
        assert result["net_profit"] == pytest.approx(0.10 - expected_gas)
        assert result["net_profit"] > 0

    def test_unprofitable_binary(self):
        """YES + NO >= $1.00 should yield zero or negative profit."""
        result = net_profit_opinion_binary(0.55, 0.50)
        assert result["net_profit"] <= 0

    def test_exact_dollar(self):
        """YES + NO = $1.00 exactly — no profit."""
        result = net_profit_opinion_binary(0.50, 0.50)
        assert result["gross_spread"] == pytest.approx(0.0)
        assert result["net_profit"] <= 0

    def test_gas_only_fees(self):
        """Opinion has no trading fee, only gas. Fees should equal 2 * gas."""
        result = net_profit_opinion_binary(0.30, 0.40)
        expected_gas = BNB_GAS_ESTIMATE * 2
        assert result["fees"] == pytest.approx(expected_gas)

    def test_small_spread_eaten_by_gas(self):
        """Very small spread consumed by gas cost."""
        # Spread = 0.01, gas = 0.02 (at default BNB_GAS_ESTIMATE=0.01)
        result = net_profit_opinion_binary(0.495, 0.495)
        assert result["gross_spread"] == pytest.approx(0.01)
        if BNB_GAS_ESTIMATE * 2 >= 0.01:
            assert result["net_profit"] <= 0


class TestOpinionMultiFees:
    def test_profitable_multi(self):
        """Sum of YES prices < $1.00 across 3 outcomes."""
        prices = [0.25, 0.25, 0.30]  # Total = 0.80
        result = net_profit_opinion_multi(prices)
        assert result["gross_spread"] == pytest.approx(0.20)
        expected_gas = BNB_GAS_ESTIMATE * 3
        assert result["fees"] == pytest.approx(expected_gas)
        assert result["net_profit"] == pytest.approx(0.20 - expected_gas)

    def test_unprofitable_multi(self):
        """Sum > $1.00 should yield negative."""
        prices = [0.40, 0.40, 0.30]
        result = net_profit_opinion_multi(prices)
        assert result["net_profit"] <= 0

    def test_gas_scales_with_outcomes(self):
        """Gas cost should be per-outcome."""
        prices = [0.10, 0.10, 0.10, 0.10, 0.10]  # 5 outcomes, total = 0.50
        result = net_profit_opinion_multi(prices)
        expected_gas = BNB_GAS_ESTIMATE * 5
        assert result["fees"] == pytest.approx(expected_gas)


class TestCrossOpinionFees:
    def test_profitable_cross(self):
        """Poly YES + Opinion NO < $1.00."""
        result = net_profit_cross_opinion(0.40, 0.45, "yes", "no")
        assert result["gross_spread"] == pytest.approx(0.15)
        assert result["net_profit"] > 0

    def test_unprofitable_cross(self):
        """Total cost >= $1.00."""
        result = net_profit_cross_opinion(0.55, 0.50, "yes", "no")
        assert result["net_profit"] <= 0


class TestOpinionBinaryScan:
    def test_finds_profitable_market(self):
        from scans.opinion import scan_opinion_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {
                "id": "mkt1",
                "title": "Will BTC hit 100k?",
                "outcomes": ["Yes", "No"],
                "yesPrice": 0.40,
                "noPrice": 0.45,
            }
        ]
        mock_client.get_market_price.return_value = (0.40, 0.45)

        opps = scan_opinion_binary(mock_client, min_profit=0.001)
        assert len(opps) == 1
        assert opps[0]["type"] == "OpinionBinary"
        assert opps[0]["_opinion_market_id"] == "mkt1"
        assert opps[0]["_opinion_yes"] == 0.40
        assert opps[0]["_opinion_no"] == 0.45

    def test_skips_unprofitable_market(self):
        from scans.opinion import scan_opinion_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {
                "id": "mkt1",
                "title": "Test Market",
                "outcomes": ["Yes", "No"],
                "yesPrice": 0.55,
                "noPrice": 0.50,
            }
        ]
        mock_client.get_market_price.return_value = (0.55, 0.50)

        opps = scan_opinion_binary(mock_client, min_profit=0.001)
        assert len(opps) == 0

    def test_no_markets(self):
        from scans.opinion import scan_opinion_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = []

        opps = scan_opinion_binary(mock_client, min_profit=0.001)
        assert len(opps) == 0

    def test_none_client(self):
        from scans.opinion import scan_opinion_binary

        opps = scan_opinion_binary(None, min_profit=0.001)
        assert len(opps) == 0

    def test_skips_non_binary(self):
        """Markets with != 2 outcomes should be skipped."""
        from scans.opinion import scan_opinion_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {
                "id": "mkt1",
                "title": "Multi Market",
                "outcomes": ["A", "B", "C"],
                "yesPrice": 0.20,
                "noPrice": 0.30,
            }
        ]

        opps = scan_opinion_binary(mock_client, min_profit=0.001)
        assert len(opps) == 0


class TestOpinionMultiScan:
    def test_finds_profitable_multi(self):
        from scans.opinion import scan_opinion_multi

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {
                "id": "mkt1",
                "title": "Who wins the election?",
                "outcomes": [
                    {"yesPrice": 0.25},
                    {"yesPrice": 0.25},
                    {"yesPrice": 0.25},
                ],
            }
        ]

        opps = scan_opinion_multi(mock_client, min_profit=0.001)
        assert len(opps) == 1
        assert opps[0]["type"] == "OpinionMulti(3)"

    def test_skips_binary_markets(self):
        """Markets with only 2 outcomes should not appear in multi scan."""
        from scans.opinion import scan_opinion_multi

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {
                "id": "mkt1",
                "title": "Binary Market",
                "outcomes": [
                    {"yesPrice": 0.40},
                    {"yesPrice": 0.40},
                ],
            }
        ]

        opps = scan_opinion_multi(mock_client, min_profit=0.001)
        assert len(opps) == 0
