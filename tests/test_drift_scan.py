"""Tests for Drift BET fee functions and scans/drift.py scan functions."""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fees import (
    net_profit_drift_binary,
    net_profit_cross_drift,
    SOLANA_GAS_ESTIMATE,
)


class TestDriftBinaryFees:
    def test_profitable_binary(self):
        """YES + NO < $1.00 should yield positive net profit minus gas."""
        result = net_profit_drift_binary(0.40, 0.50)
        assert result["gross_spread"] == pytest.approx(0.10)
        expected_gas = SOLANA_GAS_ESTIMATE * 2
        assert result["fees"] == pytest.approx(expected_gas)
        assert result["net_profit"] == pytest.approx(0.10 - expected_gas)
        assert result["net_profit"] > 0

    def test_unprofitable_binary(self):
        """YES + NO >= $1.00 should yield zero or negative."""
        result = net_profit_drift_binary(0.55, 0.50)
        assert result["net_profit"] <= 0

    def test_exact_dollar(self):
        """YES + NO = $1.00 exactly."""
        result = net_profit_drift_binary(0.50, 0.50)
        assert result["gross_spread"] == pytest.approx(0.0)
        assert result["net_profit"] <= 0

    def test_solana_gas_only(self):
        """Drift has no trading fee, only SOL gas. Fees should equal 2 * gas."""
        result = net_profit_drift_binary(0.30, 0.40)
        expected_gas = SOLANA_GAS_ESTIMATE * 2
        assert result["fees"] == pytest.approx(expected_gas)

    def test_large_spread(self):
        """Wide spread — large profit."""
        result = net_profit_drift_binary(0.20, 0.30)
        assert result["gross_spread"] == pytest.approx(0.50)
        assert result["net_profit"] > 0.49  # Gas is tiny on Solana


class TestCrossDriftFees:
    def test_profitable_cross(self):
        """Poly YES + Drift NO < $1.00."""
        result = net_profit_cross_drift(0.40, 0.45, "yes", "no")
        assert result["gross_spread"] == pytest.approx(0.15)
        assert result["net_profit"] > 0

    def test_unprofitable_cross(self):
        """Total cost >= $1.00."""
        result = net_profit_cross_drift(0.55, 0.50, "yes", "no")
        assert result["net_profit"] <= 0

    def test_fees_include_both_chains(self):
        """Cross-platform should include Polygon + Solana gas."""
        result = net_profit_cross_drift(0.30, 0.40, "yes", "no")
        # Fees include polymarket winner fee + polygon gas + solana gas
        assert result["fees"] > 0


class TestDriftBinaryScan:
    def test_finds_profitable_market(self):
        from scans.drift import scan_drift_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {
                "id": "drift-mkt-1",
                "title": "Will SOL hit $500?",
                "yesPrice": 0.40,
                "noPrice": 0.45,
            }
        ]
        mock_client.get_market_price.return_value = (0.40, 0.45)

        opps = scan_drift_binary(mock_client, min_profit=0.001)
        assert len(opps) == 1
        assert opps[0]["type"] == "DriftBinary"
        assert opps[0]["_drift_market_id"] == "drift-mkt-1"
        assert opps[0]["_drift_yes"] == 0.40
        assert opps[0]["_drift_no"] == 0.45

    def test_skips_unprofitable_market(self):
        from scans.drift import scan_drift_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {
                "id": "drift-mkt-1",
                "title": "Test Market",
                "yesPrice": 0.55,
                "noPrice": 0.50,
            }
        ]
        mock_client.get_market_price.return_value = (0.55, 0.50)

        opps = scan_drift_binary(mock_client, min_profit=0.001)
        assert len(opps) == 0

    def test_no_markets(self):
        from scans.drift import scan_drift_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = []

        opps = scan_drift_binary(mock_client, min_profit=0.001)
        assert len(opps) == 0

    def test_none_client(self):
        from scans.drift import scan_drift_binary

        opps = scan_drift_binary(None, min_profit=0.001)
        assert len(opps) == 0

    def test_skips_missing_prices(self):
        """Markets with None prices should be skipped."""
        from scans.drift import scan_drift_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {
                "id": "drift-mkt-1",
                "title": "No Prices",
            }
        ]
        mock_client.get_market_price.return_value = (None, None)

        opps = scan_drift_binary(mock_client, min_profit=0.001)
        assert len(opps) == 0

    def test_skips_dust_prices(self):
        """Markets with prices <= 0.01 should be skipped."""
        from scans.drift import scan_drift_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {
                "id": "drift-mkt-1",
                "title": "Dust Market",
                "yesPrice": 0.005,
                "noPrice": 0.005,
            }
        ]
        mock_client.get_market_price.return_value = (0.005, 0.005)

        opps = scan_drift_binary(mock_client, min_profit=0.001)
        assert len(opps) == 0
