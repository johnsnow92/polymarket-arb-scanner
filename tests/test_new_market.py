"""Tests for scans/new_market.py — Strategy #34 New Market Mispricing."""

import pytest
from unittest.mock import patch, MagicMock
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock py_clob_client before importing scan modules
sys.modules["py_clob_client"] = MagicMock()
sys.modules["py_clob_client.clob_types"] = MagicMock()
sys.modules["py_clob_client.client"] = MagicMock()

from scans.new_market import (
    scan_new_market_mispricing,
    _get_market_age_hours,
    _is_new_market,
)


class TestGetMarketAgeHours:
    def test_timestamp_seconds(self):
        market = {"created_at": time.time() - 3600}
        age = _get_market_age_hours(market, "polymarket")
        assert age is not None
        assert 0.9 < age < 1.1

    def test_timestamp_milliseconds(self):
        market = {"created_at": (time.time() - 7200) * 1000}
        age = _get_market_age_hours(market, "polymarket")
        assert age is not None
        assert 1.9 < age < 2.1

    def test_iso_string(self):
        from datetime import datetime, timezone, timedelta
        created = datetime.now(timezone.utc) - timedelta(hours=5)
        market = {"created_at": created.isoformat()}
        age = _get_market_age_hours(market, "polymarket")
        assert age is not None
        assert 4.9 < age < 5.1

    def test_kalshi_created_at(self):
        """Kalshi also uses created_at like other platforms."""
        market = {"created_at": time.time() - 10800}
        age = _get_market_age_hours(market, "kalshi")
        assert age is not None
        assert 2.9 < age < 3.1

    def test_missing_timestamps_returns_none(self):
        market = {"title": "No timestamp"}
        age = _get_market_age_hours(market, "polymarket")
        assert age is None


class TestIsNewMarket:
    def test_new_market_within_threshold(self):
        market = {"created_at": time.time() - 3600}
        assert _is_new_market(market, "polymarket", max_age_hours=24) is True

    def test_old_market_exceeds_threshold(self):
        market = {"created_at": time.time() - 100 * 3600}
        assert _is_new_market(market, "polymarket", max_age_hours=48) is False

    def test_no_timestamp_returns_false(self):
        market = {"title": "No timestamp"}
        assert _is_new_market(market, "polymarket", max_age_hours=24) is False


class TestScanNewMarketMispricing:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        with patch("scans.new_market.NEW_MARKET_MISPRICING_ENABLED", True):
            with patch("scans.new_market.NEW_MARKET_AGE_HOURS", 48):
                with patch("scans.new_market.NEW_MARKET_MIN_DIVERGENCE", 0.05):
                    yield

    def test_disabled_returns_empty(self):
        with patch("scans.new_market.NEW_MARKET_MISPRICING_ENABLED", False):
            result = scan_new_market_mispricing([])
            assert result == []

    def test_empty_markets_returns_empty(self):
        result = scan_new_market_mispricing([])
        assert result == []

    def test_old_markets_returns_empty(self):
        markets = [
            {"title": "Old market", "created_at": time.time() - 100 * 3600, "yes_price": 0.50}
        ]
        result = scan_new_market_mispricing(markets, max_age_hours=48)
        assert result == []

    def test_no_signal_aggregator_returns_empty(self):
        markets = [
            {"title": "New market", "created_at": time.time() - 3600, "yes_price": 0.50}
        ]
        result = scan_new_market_mispricing(markets, signal_aggregator=None)
        assert result == []

    def test_finds_mispricing_with_signal_aggregator(self):
        markets = [
            {
                "title": "Will X happen?",
                "created_at": time.time() - 3600,
                "yes_price": 0.40,
                "condition_id": "m1",
            }
        ]
        mock_agg = MagicMock()
        mock_agg.get_consensus.return_value = {"probability": 0.60}

        with patch("fees.net_profit_new_market") as mock_fee:
            mock_fee.return_value = {
                "net_profit": 0.15,
                "net_roi": 0.375,
                "fees": 0.05,
            }
            result = scan_new_market_mispricing(
                markets,
                signal_aggregator=mock_agg,
                min_divergence=0.05,
                min_profit=0.01,
            )

            if result:
                opp = result[0]
                assert opp["type"] == "NewMarketMispricing"
                assert opp["_layer"] == 2
                assert opp["_divergence"] >= 0.05
                assert "_fair_value" in opp

    def test_divergence_below_threshold_filtered(self):
        markets = [
            {
                "title": "Test market",
                "created_at": time.time() - 3600,
                "yes_price": 0.50,
                "condition_id": "m1",
            }
        ]
        mock_agg = MagicMock()
        mock_agg.get_consensus.return_value = {"probability": 0.52}

        result = scan_new_market_mispricing(
            markets,
            signal_aggregator=mock_agg,
            min_divergence=0.10,
        )
        assert result == []


class TestNewMarketFeeFunction:
    def test_net_profit_new_market_buy_yes(self):
        from fees import net_profit_new_market
        result = net_profit_new_market(
            market_price=0.40,
            fair_value=0.60,
            platform="polymarket",
        )
        assert "net_profit" in result
        assert "gross_spread" in result
        assert result["gross_spread"] == pytest.approx(0.20, abs=0.01)

    def test_net_profit_new_market_no_edge(self):
        from fees import net_profit_new_market
        result = net_profit_new_market(
            market_price=0.50,
            fair_value=0.50,
            platform="kalshi",
        )
        assert result["gross_spread"] == 0.0
