"""Tests for scans/api_outage.py — Strategy #35 API Outage Arbitrage."""

import pytest
from unittest.mock import patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock py_clob_client before importing scan modules
sys.modules["py_clob_client"] = MagicMock()
sys.modules["py_clob_client.clob_types"] = MagicMock()
sys.modules["py_clob_client.client"] = MagicMock()

from scans.api_outage import scan_api_outage_arb


class TestScanApiOutageArb:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        with patch("scans.api_outage.API_OUTAGE_ARB_ENABLED", True):
            with patch("scans.api_outage.API_OUTAGE_STALE_THRESHOLD", 60):
                with patch("scans.api_outage.API_OUTAGE_MIN_DIVERGENCE", 0.02):
                    yield

    def test_disabled_returns_empty(self):
        with patch("scans.api_outage.API_OUTAGE_ARB_ENABLED", False):
            result = scan_api_outage_arb([])
            assert result == []

    def test_no_feed_health_tracker_returns_empty(self):
        result = scan_api_outage_arb([], feed_health_tracker=None)
        assert result == []

    def test_no_outages_returns_empty(self):
        mock_tracker = MagicMock()
        mock_tracker.get_outage_opportunities.return_value = []
        result = scan_api_outage_arb([], feed_health_tracker=mock_tracker)
        assert result == []

    def test_finds_opportunity_during_outage(self):
        mock_tracker = MagicMock()
        mock_tracker.get_outage_opportunities.return_value = [
            {"platform": "kalshi", "outage_duration": 120}
        ]

        matches = [
            {
                "platform_a": "kalshi",
                "platform_b": "polymarket",
                "market_a": {
                    "yes_price": 0.50,
                    "id": "k1",
                    "title": "Test Market",
                },
                "market_b": {
                    "yes_price": 0.60,
                    "condition_id": "p1",
                    "title": "Test Market",
                },
            }
        ]

        with patch("fees.net_profit_stale_price") as mock_fee:
            mock_fee.return_value = {
                "net_profit": 0.08,
                "net_roi": 0.16,
                "fees": 0.02,
            }
            result = scan_api_outage_arb(
                matches,
                feed_health_tracker=mock_tracker,
                min_divergence=0.02,
                min_profit=0.01,
            )

            assert len(result) > 0
            opp = result[0]
            assert opp["type"] == "APIOutageArb"
            assert opp["_layer"] == 2
            assert opp["_stale_platform"] == "kalshi"
            assert opp["_fresh_platform"] == "polymarket"

    def test_divergence_below_threshold_filtered(self):
        mock_tracker = MagicMock()
        mock_tracker.get_outage_opportunities.return_value = [
            {"platform": "kalshi", "outage_duration": 60}
        ]

        matches = [
            {
                "platform_a": "kalshi",
                "platform_b": "polymarket",
                "market_a": {"yes_price": 0.50, "id": "k1"},
                "market_b": {"yes_price": 0.51, "condition_id": "p1"},
            }
        ]

        result = scan_api_outage_arb(
            matches,
            feed_health_tracker=mock_tracker,
            min_divergence=0.05,
        )
        assert result == []

    def test_both_platforms_in_outage_skipped(self):
        mock_tracker = MagicMock()
        mock_tracker.get_outage_opportunities.return_value = [
            {"platform": "kalshi", "outage_duration": 60},
            {"platform": "polymarket", "outage_duration": 60},
        ]

        matches = [
            {
                "platform_a": "kalshi",
                "platform_b": "polymarket",
                "market_a": {"yes_price": 0.50, "id": "k1"},
                "market_b": {"yes_price": 0.60, "condition_id": "p1"},
            }
        ]

        result = scan_api_outage_arb(
            matches,
            feed_health_tracker=mock_tracker,
        )
        assert result == []

    def test_buy_no_direction_when_fresh_lower(self):
        mock_tracker = MagicMock()
        mock_tracker.get_outage_opportunities.return_value = [
            {"platform": "kalshi", "outage_duration": 120}
        ]

        matches = [
            {
                "platform_a": "kalshi",
                "platform_b": "polymarket",
                "market_a": {
                    "yes_price": 0.70,
                    "id": "k1",
                    "title": "Test",
                },
                "market_b": {
                    "yes_price": 0.55,
                    "condition_id": "p1",
                    "title": "Test",
                },
            }
        ]

        with patch("fees.net_profit_stale_price") as mock_fee:
            mock_fee.return_value = {
                "net_profit": 0.10,
                "net_roi": 0.33,
            }
            result = scan_api_outage_arb(
                matches,
                feed_health_tracker=mock_tracker,
                min_divergence=0.05,
                min_profit=0.01,
            )

            if result:
                assert result[0]["_direction"] == "BUY_NO"
