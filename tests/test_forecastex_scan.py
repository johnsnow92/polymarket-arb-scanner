"""Tests for ForecastEx (IBKR) scan and fee functions."""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fees import net_profit_forecastex_binary, forecastex_commission


class TestForecastExFees:
    def test_commission_two_contracts(self):
        """Default commission: $0.01 per contract, 2 contracts."""
        fee = forecastex_commission(2, 0.01)
        assert fee == pytest.approx(0.02)

    def test_commission_custom_rate(self):
        """Custom commission rate."""
        fee = forecastex_commission(2, 0.05)
        assert fee == pytest.approx(0.10)

    def test_binary_profitable(self):
        """YES + NO < $1.00 with small commission."""
        result = net_profit_forecastex_binary(0.40, 0.50)
        assert result["gross_spread"] == pytest.approx(0.10)
        assert result["fees"] == pytest.approx(0.02)  # 2 * $0.01
        assert result["net_profit"] == pytest.approx(0.08)

    def test_binary_unprofitable(self):
        """YES + NO >= $1.00."""
        result = net_profit_forecastex_binary(0.55, 0.50)
        assert result["net_profit"] <= 0

    def test_binary_exact_dollar(self):
        """YES + NO = $1.00 exactly."""
        result = net_profit_forecastex_binary(0.50, 0.50)
        assert result["net_profit"] <= 0

    def test_binary_custom_commission(self):
        """With higher commission rate."""
        result = net_profit_forecastex_binary(0.40, 0.50, commission_per_contract=0.05)
        assert result["gross_spread"] == pytest.approx(0.10)
        assert result["fees"] == pytest.approx(0.10)  # 2 * $0.05
        assert result["net_profit"] == pytest.approx(0.0)

    def test_binary_large_spread(self):
        """Large spread should be profitable even with fees."""
        result = net_profit_forecastex_binary(0.20, 0.30)
        assert result["gross_spread"] == pytest.approx(0.50)
        assert result["net_profit"] > 0.40


class TestForecastExScan:
    def test_finds_profitable_market(self):
        from scans.forecastex import scan_forecastex_binary

        mock_client = MagicMock()
        mock_client.authenticated = True
        mock_client.fetch_all_markets.return_value = [
            {"id": "mkt1", "title": "Will X happen?",
             "yesPrice": 0.40, "noPrice": 0.50, "volume": 1000},
        ]
        mock_client.get_market_price.return_value = (0.40, 0.50)

        opps = scan_forecastex_binary(mock_client, min_profit=0.001)
        assert len(opps) == 1
        assert opps[0]["type"] == "ForecastExBinary"
        assert opps[0]["_fx_buy_only"] is True
        assert opps[0]["net_profit"] > 0

    def test_skips_unprofitable(self):
        from scans.forecastex import scan_forecastex_binary

        mock_client = MagicMock()
        mock_client.authenticated = True
        mock_client.fetch_all_markets.return_value = [
            {"id": "mkt1", "title": "Tight market",
             "yesPrice": 0.55, "noPrice": 0.50, "volume": 100},
        ]
        mock_client.get_market_price.return_value = (0.55, 0.50)

        opps = scan_forecastex_binary(mock_client, min_profit=0.001)
        assert len(opps) == 0

    def test_not_authenticated(self):
        from scans.forecastex import scan_forecastex_binary

        mock_client = MagicMock()
        mock_client.authenticated = False

        opps = scan_forecastex_binary(mock_client, min_profit=0.001)
        assert len(opps) == 0

    def test_no_markets(self):
        from scans.forecastex import scan_forecastex_binary

        mock_client = MagicMock()
        mock_client.authenticated = True
        mock_client.fetch_all_markets.return_value = []

        opps = scan_forecastex_binary(mock_client, min_profit=0.001)
        assert len(opps) == 0

    def test_none_client(self):
        from scans.forecastex import scan_forecastex_binary
        opps = scan_forecastex_binary(None, min_profit=0.001)
        assert len(opps) == 0

    def test_skips_extreme_prices(self):
        """Prices at 0.01 or 0.99 should be skipped."""
        from scans.forecastex import scan_forecastex_binary

        mock_client = MagicMock()
        mock_client.authenticated = True
        mock_client.fetch_all_markets.return_value = [
            {"id": "mkt1", "title": "Edge case", "yesPrice": 0.01, "noPrice": 0.01},
        ]
        mock_client.get_market_price.return_value = (0.01, 0.01)

        opps = scan_forecastex_binary(mock_client, min_profit=0.001)
        assert len(opps) == 0
