"""Tests for scans/predictit.py — PredictIt standalone arbitrage scans."""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fees import net_profit_predictit_binary, net_profit_predictit_multi


class TestPredictItFees:
    def test_binary_profitable(self):
        """YES + NO < $1.00 after 10% profit fee."""
        result = net_profit_predictit_binary(0.40, 0.40)
        assert result["gross_spread"] == pytest.approx(0.20)
        assert result["net_profit"] > 0
        # Fee = 10% of winner profit (1.0 - 0.40 = 0.60, fee = 0.06)
        assert result["fees"] == pytest.approx(0.06)
        assert result["net_profit"] == pytest.approx(0.14)

    def test_binary_unprofitable(self):
        result = net_profit_predictit_binary(0.55, 0.50)
        assert result["net_profit"] < 0

    def test_multi_profitable(self):
        """Sum of YES < $1.00 across 3 contracts."""
        result = net_profit_predictit_multi([0.25, 0.25, 0.25])
        assert result["gross_spread"] == pytest.approx(0.25)
        assert result["net_profit"] > 0
        # Fee = 10% of (1.0 - 0.25) = 0.075
        assert result["fees"] == pytest.approx(0.075)

    def test_multi_unprofitable(self):
        result = net_profit_predictit_multi([0.40, 0.40, 0.30])
        assert result["net_profit"] < 0

    def test_multi_uses_cheapest_for_fee(self):
        """Worst case: cheapest outcome wins, highest profit, highest fee."""
        result = net_profit_predictit_multi([0.10, 0.30, 0.30])
        # Fee = 10% of (1.0 - 0.10) = 0.09
        assert result["fees"] == pytest.approx(0.09)


class TestPredictItBinaryScan:
    def test_finds_binary_arb(self):
        from scans.predictit import scan_predictit_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {
                "name": "Test Binary Market",
                "contracts": [{
                    "id": 123,
                    "bestBuyYesCost": 0.40,
                    "bestBuyNoCost": 0.40,
                }],
            },
        ]

        opps = scan_predictit_binary(mock_client, min_profit=0.001)
        assert len(opps) == 1
        assert opps[0]["type"] == "PIBinary"
        assert opps[0]["_contract_id"] == 123

    def test_skips_multi_contract_markets(self):
        from scans.predictit import scan_predictit_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {
                "name": "Multi Contract Market",
                "contracts": [
                    {"id": 1, "bestBuyYesCost": 0.30, "bestBuyNoCost": 0.30},
                    {"id": 2, "bestBuyYesCost": 0.20, "bestBuyNoCost": 0.20},
                ],
            },
        ]

        opps = scan_predictit_binary(mock_client, min_profit=0.001)
        assert len(opps) == 0

    def test_skips_no_prices(self):
        from scans.predictit import scan_predictit_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {"name": "No Prices", "contracts": [{"id": 1}]},
        ]

        opps = scan_predictit_binary(mock_client, min_profit=0.001)
        assert len(opps) == 0


class TestPredictItMultiScan:
    def test_finds_multi_arb(self):
        from scans.predictit import scan_predictit_multi

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {
                "name": "Multi Outcome Market",
                "contracts": [
                    {"id": 1, "bestBuyYesCost": 0.25},
                    {"id": 2, "bestBuyYesCost": 0.25},
                    {"id": 3, "bestBuyYesCost": 0.25},
                ],
            },
        ]

        opps = scan_predictit_multi(mock_client, min_profit=0.001)
        assert len(opps) == 1
        assert opps[0]["type"] == "PIMulti(3)"
        assert len(opps[0]["_pi_contract_ids"]) == 3

    def test_skips_single_contract(self):
        from scans.predictit import scan_predictit_multi

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {"name": "Single", "contracts": [{"id": 1, "bestBuyYesCost": 0.40}]},
        ]

        opps = scan_predictit_multi(mock_client, min_profit=0.001)
        assert len(opps) == 0
