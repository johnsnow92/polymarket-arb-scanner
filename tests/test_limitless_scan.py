"""Tests for Limitless fee functions and scans/limitless.py scan functions."""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fees import (
    limitless_dynamic_fee,
    net_profit_limitless_binary,
    net_profit_cross_limitless,
    BASE_GAS_ESTIMATE,
)


class TestLimitlessDynamicFee:
    def test_zero_profit_zero_fee(self):
        """No profit means no fee."""
        assert limitless_dynamic_fee(0.0, days_to_resolution=7.0) == 0.0

    def test_negative_profit_zero_fee(self):
        """Negative profit means no fee."""
        assert limitless_dynamic_fee(-0.05, days_to_resolution=7.0) == 0.0

    def test_very_short_resolution(self):
        """<1 day resolution = 0.03% rate."""
        fee = limitless_dynamic_fee(1.0, days_to_resolution=0.5)
        assert fee == pytest.approx(0.0003)

    def test_one_day_resolution(self):
        """Exactly 1 day = 0.03% rate (boundary)."""
        fee = limitless_dynamic_fee(1.0, days_to_resolution=1.0)
        assert fee == pytest.approx(0.0003)

    def test_seven_day_resolution(self):
        """7 days = ~0.5% rate."""
        fee = limitless_dynamic_fee(1.0, days_to_resolution=7.0)
        assert fee == pytest.approx(0.005)

    def test_thirty_day_resolution(self):
        """30 days = ~1.5% rate."""
        fee = limitless_dynamic_fee(1.0, days_to_resolution=30.0)
        assert fee == pytest.approx(0.015)

    def test_long_resolution_capped(self):
        """Very long resolution should cap at 3%."""
        fee = limitless_dynamic_fee(1.0, days_to_resolution=365.0)
        assert fee <= 0.03

    def test_fee_scales_with_profit(self):
        """Fee should scale proportionally with profit amount."""
        fee_small = limitless_dynamic_fee(0.10, days_to_resolution=7.0)
        fee_large = limitless_dynamic_fee(1.00, days_to_resolution=7.0)
        assert fee_large == pytest.approx(fee_small * 10)

    def test_monotonic_increase_with_days(self):
        """Fee rate should increase with more days to resolution."""
        fee_1d = limitless_dynamic_fee(1.0, days_to_resolution=1.0)
        fee_7d = limitless_dynamic_fee(1.0, days_to_resolution=7.0)
        fee_30d = limitless_dynamic_fee(1.0, days_to_resolution=30.0)
        fee_60d = limitless_dynamic_fee(1.0, days_to_resolution=60.0)
        assert fee_1d < fee_7d < fee_30d < fee_60d


class TestLimitlessBinaryFees:
    def test_profitable_binary(self):
        """YES + NO < $1.00 should yield positive net profit."""
        result = net_profit_limitless_binary(0.40, 0.50, days_to_resolution=7.0)
        assert result["gross_spread"] == pytest.approx(0.10)
        assert result["net_profit"] > 0

    def test_unprofitable_binary(self):
        """YES + NO >= $1.00 should yield zero or negative."""
        result = net_profit_limitless_binary(0.55, 0.50, days_to_resolution=7.0)
        assert result["net_profit"] <= 0

    def test_exact_dollar(self):
        """YES + NO = $1.00 exactly."""
        result = net_profit_limitless_binary(0.50, 0.50, days_to_resolution=7.0)
        assert result["net_profit"] <= 0

    def test_fees_include_dynamic_and_gas(self):
        """Fees should include both dynamic fee and Base gas."""
        result = net_profit_limitless_binary(0.40, 0.50, days_to_resolution=7.0)
        spread = 0.10
        dynamic_fee = limitless_dynamic_fee(spread, 7.0)
        gas = BASE_GAS_ESTIMATE * 2
        assert result["fees"] == pytest.approx(dynamic_fee + gas)

    def test_short_resolution_lower_fees(self):
        """Short resolution should mean lower fees and higher profit."""
        result_short = net_profit_limitless_binary(0.40, 0.50, days_to_resolution=1.0)
        result_long = net_profit_limitless_binary(0.40, 0.50, days_to_resolution=30.0)
        assert result_short["net_profit"] > result_long["net_profit"]

    def test_long_resolution_higher_fees(self):
        """Long-dated markets should have higher fees."""
        result_short = net_profit_limitless_binary(0.30, 0.40, days_to_resolution=1.0)
        result_long = net_profit_limitless_binary(0.30, 0.40, days_to_resolution=60.0)
        assert result_long["fees"] > result_short["fees"]


class TestCrossLimitlessFees:
    def test_profitable_cross(self):
        """Poly YES + Limitless NO < $1.00."""
        result = net_profit_cross_limitless(0.40, 0.45, "yes", "no", days_to_resolution=7.0)
        assert result["gross_spread"] == pytest.approx(0.15)
        assert result["net_profit"] > 0

    def test_unprofitable_cross(self):
        """Total cost >= $1.00."""
        result = net_profit_cross_limitless(0.55, 0.50, "yes", "no", days_to_resolution=7.0)
        assert result["net_profit"] <= 0

    def test_fees_include_both_chains(self):
        """Cross-platform should include Polygon + Base gas."""
        result = net_profit_cross_limitless(0.30, 0.40, "yes", "no", days_to_resolution=7.0)
        assert result["fees"] > 0


class TestLimitlessBinaryScan:
    def test_finds_profitable_market(self):
        from scans.limitless import scan_limitless_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {
                "id": "lim-mkt-1",
                "title": "Will ETH merge succeed?",
                "yesPrice": 0.40,
                "noPrice": 0.45,
                "resolutionDate": "2026-02-20T00:00:00Z",
            }
        ]
        mock_client.get_market_price.return_value = (0.40, 0.45)

        opps = scan_limitless_binary(mock_client, min_profit=0.001)
        assert len(opps) == 1
        assert opps[0]["type"] == "LimitlessBinary"
        assert opps[0]["_limitless_market_id"] == "lim-mkt-1"
        assert opps[0]["_limitless_yes"] == 0.40
        assert opps[0]["_limitless_no"] == 0.45
        assert "_days_to_resolution" in opps[0]

    def test_skips_unprofitable_market(self):
        from scans.limitless import scan_limitless_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {
                "id": "lim-mkt-1",
                "title": "Test Market",
                "yesPrice": 0.55,
                "noPrice": 0.50,
            }
        ]
        mock_client.get_market_price.return_value = (0.55, 0.50)

        opps = scan_limitless_binary(mock_client, min_profit=0.001)
        assert len(opps) == 0

    def test_no_markets(self):
        from scans.limitless import scan_limitless_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = []

        opps = scan_limitless_binary(mock_client, min_profit=0.001)
        assert len(opps) == 0

    def test_none_client(self):
        from scans.limitless import scan_limitless_binary

        opps = scan_limitless_binary(None, min_profit=0.001)
        assert len(opps) == 0

    def test_market_without_resolution_date(self):
        """Markets without a resolution date should use default 7.0 days."""
        from scans.limitless import scan_limitless_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {
                "id": "lim-mkt-1",
                "title": "No Date Market",
                "yesPrice": 0.40,
                "noPrice": 0.45,
            }
        ]
        mock_client.get_market_price.return_value = (0.40, 0.45)

        opps = scan_limitless_binary(mock_client, min_profit=0.001)
        assert len(opps) == 1
        assert opps[0]["_days_to_resolution"] == 7.0

    def test_skips_dust_prices(self):
        """Markets with prices <= 0.01 should be skipped."""
        from scans.limitless import scan_limitless_binary

        mock_client = MagicMock()
        mock_client.fetch_all_markets.return_value = [
            {
                "id": "lim-mkt-1",
                "title": "Dust Market",
                "yesPrice": 0.005,
                "noPrice": 0.005,
            }
        ]
        mock_client.get_market_price.return_value = (0.005, 0.005)

        opps = scan_limitless_binary(mock_client, min_profit=0.001)
        assert len(opps) == 0


class TestLimitlessDaysToResolution:
    def test_with_resolution_date(self):
        """Should parse resolutionDate correctly."""
        from scans.limitless import _days_to_resolution_limitless

        # Far future date — should be > 0
        market = {"resolutionDate": "2030-01-01T00:00:00Z"}
        days = _days_to_resolution_limitless(market)
        assert days > 365

    def test_without_resolution_date(self):
        """Should default to 7.0 when no date available."""
        from scans.limitless import _days_to_resolution_limitless

        market = {}
        days = _days_to_resolution_limitless(market)
        assert days == 7.0

    def test_past_date_floors(self):
        """Past dates should floor at 0.01."""
        from scans.limitless import _days_to_resolution_limitless

        market = {"resolutionDate": "2020-01-01T00:00:00Z"}
        days = _days_to_resolution_limitless(market)
        assert days == pytest.approx(0.01)

    def test_invalid_date(self):
        """Invalid date string should default to 7.0."""
        from scans.limitless import _days_to_resolution_limitless

        market = {"resolutionDate": "not-a-date"}
        days = _days_to_resolution_limitless(market)
        assert days == 7.0
