"""Tests for scans/cross_category.py — Strategy #43 Cross-Category Correlation."""

import pytest
from unittest.mock import patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock py_clob_client before importing scan modules
sys.modules["py_clob_client"] = MagicMock()
sys.modules["py_clob_client.clob_types"] = MagicMock()
sys.modules["py_clob_client.client"] = MagicMock()

from scans.cross_category import (
    scan_cross_category,
    _calculate_implied_prob,
    _match_rule,
    ExternalSignalFetcher,
    get_signal_fetcher,
)


class TestCalculateImpliedProb:
    def test_above_threshold_already_passed(self):
        prob = _calculate_implied_prob(
            current_value=110000,
            threshold=100000,
            direction="above",
        )
        assert prob == 0.90

    def test_above_threshold_close(self):
        prob = _calculate_implied_prob(
            current_value=95000,
            threshold=100000,
            direction="above",
        )
        assert 0.50 <= prob <= 0.90

    def test_above_threshold_far(self):
        prob = _calculate_implied_prob(
            current_value=50000,
            threshold=100000,
            direction="above",
        )
        assert prob < 0.30

    def test_below_direction(self):
        prob = _calculate_implied_prob(
            current_value=90000,
            threshold=100000,
            direction="below",
        )
        assert prob > 0.50


class TestMatchRule:
    def test_matches_btc_100k(self):
        rule = _match_rule("Will Bitcoin reach $100k by year end?")
        assert rule is not None
        assert rule.name == "btc_100k"

    def test_matches_eth_10k(self):
        rule = _match_rule("Ethereum ETH to hit 10k in 2026")
        assert rule is not None
        assert rule.name == "eth_10k"

    def test_no_match_for_unrelated(self):
        rule = _match_rule("Will Biden win the election?")
        assert rule is None


class TestExternalSignalFetcher:
    def test_get_cached_returns_none_initially(self):
        fetcher = ExternalSignalFetcher()
        assert fetcher._get_cached("btc_price") is None

    def test_set_and_get_cached(self):
        fetcher = ExternalSignalFetcher(cache_ttl=60.0)
        fetcher._set_cached("btc_price", 95000.0)
        cached = fetcher._get_cached("btc_price")
        assert cached == 95000.0

    def test_get_signal_btc(self):
        fetcher = ExternalSignalFetcher()
        with patch.object(fetcher, "get_btc_price", return_value=95000.0):
            signal = fetcher.get_signal("btc_price")
            assert signal == 95000.0

    def test_get_signal_eth(self):
        fetcher = ExternalSignalFetcher()
        with patch.object(fetcher, "get_eth_price", return_value=4000.0):
            signal = fetcher.get_signal("eth_price")
            assert signal == 4000.0

    def test_get_signal_unknown(self):
        fetcher = ExternalSignalFetcher()
        signal = fetcher.get_signal("unknown_signal")
        assert signal is None


class TestScanCrossCategory:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        with patch("scans.cross_category.CROSS_CATEGORY_ENABLED", True):
            with patch("scans.cross_category.CROSS_CATEGORY_MIN_DIVERGENCE", 0.10):
                yield

    def test_disabled_returns_empty(self):
        with patch("scans.cross_category.CROSS_CATEGORY_ENABLED", False):
            result = scan_cross_category([])
            assert result == []

    def test_empty_markets_returns_empty(self):
        result = scan_cross_category([])
        assert result == []

    def test_no_matching_rule_returns_empty(self):
        markets = [
            {
                "title": "Will Biden win?",
                "yes_price": 0.50,
                "condition_id": "m1",
            }
        ]
        result = scan_cross_category(markets)
        assert result == []

    def test_finds_divergence_opportunity(self):
        markets = [
            {
                "title": "Bitcoin BTC reaches 100k by December",
                "yes_price": 0.30,
                "condition_id": "m1",
            }
        ]

        mock_fetcher = MagicMock()
        mock_fetcher.get_signal.return_value = 95000.0

        with patch("fees.net_profit_cross_category") as mock_fee:
            mock_fee.return_value = {
                "net_profit": 0.30,
                "net_roi": 1.0,
            }
            result = scan_cross_category(
                markets,
                signal_fetcher=mock_fetcher,
                min_divergence=0.10,
                min_profit=0.01,
            )

            assert result, "Expected a cross-category opportunity for this fixture"
            opp = result[0]
            assert opp["type"] == "CrossCategory"
            assert opp["_layer"] == 4
            assert "_signal_value" in opp
            assert "_divergence" in opp

    def test_low_divergence_filtered(self):
        markets = [
            {
                "title": "Bitcoin BTC reaches 100k",
                "yes_price": 0.65,
                "condition_id": "m1",
            }
        ]

        mock_fetcher = MagicMock()
        mock_fetcher.get_signal.return_value = 90000.0

        result = scan_cross_category(
            markets,
            signal_fetcher=mock_fetcher,
            min_divergence=0.30,
        )
        assert result == []


class TestGetSignalFetcher:
    def test_returns_singleton(self):
        fetcher1 = get_signal_fetcher()
        fetcher2 = get_signal_fetcher()
        assert fetcher1 is fetcher2


class TestCrossCategoryFeeFunction:
    def test_net_profit_cross_category(self):
        from fees import net_profit_cross_category
        result = net_profit_cross_category(
            market_price=0.40,
            implied_prob=0.70,
            platform="polymarket",
        )
        assert "net_profit" in result
        assert "gross_spread" in result
        assert result["gross_spread"] == pytest.approx(0.30, abs=0.01)
