"""Tests for scans/expert_divergence.py — Strategy #40 Expert Divergence."""

import pytest
from unittest.mock import patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock py_clob_client before importing scan modules
sys.modules["py_clob_client"] = MagicMock()
sys.modules["py_clob_client.clob_types"] = MagicMock()
sys.modules["py_clob_client.client"] = MagicMock()

from scans.expert_divergence import scan_expert_divergence


class TestScanExpertDivergence:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        with patch("scans.expert_divergence.EXPERT_DIVERGENCE_ENABLED", True):
            with patch("scans.expert_divergence.EXPERT_DIVERGENCE_MIN_DIVERGENCE", 0.10):
                with patch("scans.expert_divergence.EXPERT_DIVERGENCE_MIN_FORECASTERS", 3):
                    yield

    def test_disabled_returns_empty(self):
        with patch("scans.expert_divergence.EXPERT_DIVERGENCE_ENABLED", False):
            result = scan_expert_divergence([])
            assert result == []

    def test_no_client_returns_empty(self):
        result = scan_expert_divergence([], superforecaster_client=None)
        assert result == []

    def test_empty_markets_returns_empty(self):
        client = MagicMock()
        result = scan_expert_divergence([], superforecaster_client=client)
        assert result == []

    def test_no_forecast_match_returns_empty(self):
        client = MagicMock()
        client.get_aggregated_expert_forecast.return_value = None

        markets = [
            {
                "title": "Will X happen?",
                "yes_price": 0.50,
                "condition_id": "m1",
            }
        ]

        result = scan_expert_divergence(markets, superforecaster_client=client)
        assert result == []

    def test_finds_divergence_opportunity(self):
        client = MagicMock()
        client.get_aggregated_expert_forecast.return_value = {
            "probability": 0.75,
            "num_sources": 5,
            "confidence": 0.70,
        }

        markets = [
            {
                "title": "Will X happen?",
                "yes_price": 0.50,
                "condition_id": "m1",
            }
        ]

        with patch("fees.net_profit_expert_divergence") as mock_fee:
            mock_fee.return_value = {
                "net_profit": 0.20,
                "net_roi": 0.40,
            }
            result = scan_expert_divergence(
                markets,
                superforecaster_client=client,
                min_divergence=0.10,
                min_profit=0.01,
            )

            assert len(result) > 0
            opp = result[0]
            assert opp["type"] == "ExpertDivergence"
            assert opp["_layer"] == 4
            assert opp["_direction"] == "BUY_YES"
            assert opp["_expert_prob"] == 0.75

    def test_buy_no_direction_when_expert_lower(self):
        client = MagicMock()
        client.get_aggregated_expert_forecast.return_value = {
            "probability": 0.30,
            "num_sources": 4,
            "confidence": 0.65,
        }

        markets = [
            {
                "title": "Will X happen?",
                "yes_price": 0.55,
                "condition_id": "m1",
            }
        ]

        with patch("fees.net_profit_expert_divergence") as mock_fee:
            mock_fee.return_value = {
                "net_profit": 0.15,
                "net_roi": 0.33,
            }
            result = scan_expert_divergence(
                markets,
                superforecaster_client=client,
                min_divergence=0.10,
                min_profit=0.01,
            )

            assert len(result) > 0
            assert result[0]["_direction"] == "BUY_NO"

    def test_low_divergence_filtered(self):
        client = MagicMock()
        client.get_aggregated_expert_forecast.return_value = {
            "probability": 0.52,
            "num_sources": 5,
            "confidence": 0.60,
        }

        markets = [
            {
                "title": "Test",
                "yes_price": 0.50,
                "condition_id": "m1",
            }
        ]

        result = scan_expert_divergence(
            markets,
            superforecaster_client=client,
            min_divergence=0.10,
        )
        assert result == []

    def test_includes_metaculus_signal(self):
        client = MagicMock()
        client.get_aggregated_expert_forecast.return_value = {
            "probability": 0.80,
            "num_sources": 3,
            "confidence": 0.75,
        }

        metaculus = MagicMock()
        markets = [
            {
                "title": "Test question",
                "yes_price": 0.50,
                "condition_id": "m1",
            }
        ]

        with patch("fees.net_profit_expert_divergence") as mock_fee:
            mock_fee.return_value = {"net_profit": 0.25, "net_roi": 0.50}
            result = scan_expert_divergence(
                markets,
                superforecaster_client=client,
                metaculus_client=metaculus,
                min_profit=0.01,
            )

            client.get_aggregated_expert_forecast.assert_called_once()
            call_kwargs = client.get_aggregated_expert_forecast.call_args[1]
            assert call_kwargs.get("metaculus_client") == metaculus


class TestExpertDivergenceFeeFunction:
    def test_net_profit_expert_divergence(self):
        from fees import net_profit_expert_divergence
        result = net_profit_expert_divergence(
            market_price=0.50,
            expert_prob=0.75,
            platform="polymarket",
        )
        assert "net_profit" in result
        assert "gross_spread" in result
        assert result["gross_spread"] == pytest.approx(0.25, abs=0.01)

    def test_no_edge_zero_profit(self):
        from fees import net_profit_expert_divergence
        result = net_profit_expert_divergence(
            market_price=0.60,
            expert_prob=0.60,
            platform="kalshi",
        )
        assert result["gross_spread"] == 0.0
