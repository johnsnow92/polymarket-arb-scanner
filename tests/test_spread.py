"""Tests for scans/spread.py — intra-platform spread capture."""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fees import net_profit_spread_polymarket


class TestSpreadFees:
    def test_polymarket_spread_profitable(self):
        """Spread where bid > ask by more than gas should be profitable."""
        result = net_profit_spread_polymarket(ask=0.40, bid=0.50)
        assert result["gross_spread"] == pytest.approx(0.10)
        assert result["net_profit"] > 0
        # Fees are just gas (2 * POLYGON_GAS_ESTIMATE)
        assert result["fees"] > 0

    def test_polymarket_spread_unprofitable(self):
        """Spread where bid <= ask should have non-positive profit."""
        result = net_profit_spread_polymarket(ask=0.50, bid=0.40)
        assert result["net_profit"] <= 0

    def test_polymarket_spread_equal(self):
        """Bid == ask should have zero profit."""
        result = net_profit_spread_polymarket(ask=0.50, bid=0.50)
        assert result["net_profit"] <= 0


class TestSpreadScanPolymarket:
    @patch("scans.spread.fetch_order_book")
    @patch("scans.spread.get_best_bid_ask")
    @patch("scans.spread.get_binary_markets")
    @patch("scans.spread._within_resolution_window", return_value=True)
    def test_finds_crossed_book(self, mock_window, mock_binary, mock_ba, mock_book):
        from scans.spread import scan_spread_polymarket

        mock_binary.return_value = [{
            "question": "Test Market",
            "clobTokenIds": '["token1", "token2"]',
        }]
        mock_book.return_value = {"bids": [{"price": "0.60", "size": "100"}],
                                  "asks": [{"price": "0.40", "size": "100"}]}
        mock_ba.return_value = {"bid": 0.60, "bid_size": 100, "ask": 0.40, "ask_size": 100}

        opps = scan_spread_polymarket([{}], min_profit=0.001)
        # Should find opportunities since bid (0.60) > ask (0.40)
        assert len(opps) >= 1
        assert opps[0]["type"] == "SpreadPM"

    @patch("scans.spread.fetch_order_book")
    @patch("scans.spread.get_best_bid_ask")
    @patch("scans.spread.get_binary_markets")
    @patch("scans.spread._within_resolution_window", return_value=True)
    def test_skips_normal_book(self, mock_window, mock_binary, mock_ba, mock_book):
        from scans.spread import scan_spread_polymarket

        mock_binary.return_value = [{
            "question": "Test Market",
            "clobTokenIds": '["token1", "token2"]',
        }]
        mock_book.return_value = {"bids": [{"price": "0.39", "size": "100"}],
                                  "asks": [{"price": "0.41", "size": "100"}]}
        mock_ba.return_value = {"bid": 0.39, "bid_size": 100, "ask": 0.41, "ask_size": 100}

        opps = scan_spread_polymarket([{}], min_profit=0.001)
        assert len(opps) == 0
