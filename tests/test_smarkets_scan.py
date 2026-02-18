"""Tests for Smarkets fee calculations and scan modules."""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fees import (
    smarkets_commission,
    net_profit_smarkets_backall,
    net_profit_smarkets_backlay,
    net_profit_cross_smarkets,
)


class TestSmarketsFees:
    def test_commission_on_positive_winnings(self):
        """2% commission on net winnings."""
        assert smarkets_commission(1.0, 0.02) == pytest.approx(0.02)

    def test_commission_zero_on_loss(self):
        """No commission when net winnings <= 0."""
        assert smarkets_commission(-0.50, 0.02) == 0.0
        assert smarkets_commission(0.0, 0.02) == 0.0

    def test_commission_custom_rate(self):
        """Custom commission rate."""
        assert smarkets_commission(1.0, 0.05) == pytest.approx(0.05)

    def test_backall_profitable(self):
        """Under-round book: sum of implied probs < 1.0."""
        probs = [0.30, 0.30, 0.30]  # Total = 0.90
        result = net_profit_smarkets_backall(probs, commission_rate=0.02)
        assert result["gross_spread"] == pytest.approx(0.10)
        assert result["net_profit"] > 0
        # Fee = 2% of (1.0 - 0.30) = 0.014
        assert result["fees"] == pytest.approx(0.014)

    def test_backall_unprofitable(self):
        """Over-round book: sum > 1.0."""
        probs = [0.40, 0.40, 0.30]
        result = net_profit_smarkets_backall(probs, commission_rate=0.02)
        assert result["net_profit"] <= 0

    def test_backall_exact_round(self):
        """Exactly 1.0 = no profit."""
        probs = [0.50, 0.30, 0.20]
        result = net_profit_smarkets_backall(probs, commission_rate=0.02)
        assert result["net_profit"] <= 0

    def test_backall_uses_cheapest_for_fee(self):
        """Fee is calculated on the cheapest (highest profit) runner."""
        probs = [0.10, 0.30, 0.40]  # Total = 0.80, cheapest = 0.10
        result = net_profit_smarkets_backall(probs, commission_rate=0.02)
        # Fee = 2% of (1.0 - 0.10) = 0.018
        assert result["fees"] == pytest.approx(0.018)

    def test_backlay_profitable(self):
        """Crossed book: back_prob < lay_prob."""
        result = net_profit_smarkets_backlay(0.40, 0.50, commission_rate=0.02)
        assert result["gross_spread"] == pytest.approx(0.10)
        # Fee = 2% of 0.10 = 0.002
        assert result["fees"] == pytest.approx(0.002)
        assert result["net_profit"] == pytest.approx(0.098)

    def test_backlay_not_crossed(self):
        """Normal book: lay_prob <= back_prob."""
        result = net_profit_smarkets_backlay(0.50, 0.40, commission_rate=0.02)
        assert result["net_profit"] == 0

    def test_backlay_zero_commission(self):
        """With 0% commission, full spread is profit."""
        result = net_profit_smarkets_backlay(0.40, 0.50, commission_rate=0.0)
        assert result["net_profit"] == pytest.approx(0.10)

    def test_cross_smarkets_profitable(self):
        """Cross-platform Poly vs Smarkets with positive spread."""
        result = net_profit_cross_smarkets(0.30, 0.30, "yes", "no", commission_rate=0.02)
        assert result["gross_spread"] == pytest.approx(0.40)
        assert result["net_profit"] > 0

    def test_cross_smarkets_unprofitable(self):
        """Cross-platform with total >= 1.0."""
        result = net_profit_cross_smarkets(0.55, 0.50, "yes", "no", commission_rate=0.02)
        assert result["net_profit"] <= 0


class TestSmarketsBackAllScan:
    def test_finds_underround_book(self):
        from scans.smarkets import scan_smarkets_backall

        mock_client = MagicMock()
        mock_client.authenticated = True
        mock_client.fetch_all_markets.return_value = [
            {"id": "mkt1", "name": "Winner", "_event": {"name": "Test Event"}}
        ]
        # Under-round: 25% + 25% = 50% total
        mock_client.list_runners.return_value = [
            {
                "id": "c1",
                "name": "Runner A",
                "quotes": {
                    "best_available_to_back": {"price": 25, "quantity": 100},
                },
            },
            {
                "id": "c2",
                "name": "Runner B",
                "quotes": {
                    "best_available_to_back": {"price": 25, "quantity": 100},
                },
            },
        ]

        opps = scan_smarkets_backall(mock_client, min_profit=0.001)
        assert len(opps) == 1
        assert opps[0]["type"] == "SmarketsBackAll"
        assert opps[0]["net_profit"] > 0

    def test_skips_overround_book(self):
        from scans.smarkets import scan_smarkets_backall

        mock_client = MagicMock()
        mock_client.authenticated = True
        mock_client.fetch_all_markets.return_value = [
            {"id": "mkt1", "name": "Winner", "_event": {"name": "Test"}}
        ]
        # Over-round: 60% + 60% = 120%
        mock_client.list_runners.return_value = [
            {
                "id": "c1",
                "quotes": {
                    "best_available_to_back": {"price": 60, "quantity": 100},
                },
            },
            {
                "id": "c2",
                "quotes": {
                    "best_available_to_back": {"price": 60, "quantity": 100},
                },
            },
        ]

        opps = scan_smarkets_backall(mock_client, min_profit=0.001)
        assert len(opps) == 0

    def test_skips_missing_back_price(self):
        from scans.smarkets import scan_smarkets_backall

        mock_client = MagicMock()
        mock_client.authenticated = True
        mock_client.fetch_all_markets.return_value = [
            {"id": "mkt1", "name": "Winner"}
        ]
        # One runner missing back price
        mock_client.list_runners.return_value = [
            {
                "id": "c1",
                "quotes": {
                    "best_available_to_back": {"price": 25, "quantity": 100},
                },
            },
            {
                "id": "c2",
                "quotes": {},  # no back price
            },
        ]

        opps = scan_smarkets_backall(mock_client, min_profit=0.001)
        assert len(opps) == 0

    def test_not_authenticated(self):
        from scans.smarkets import scan_smarkets_backall

        mock_client = MagicMock()
        mock_client.authenticated = False

        opps = scan_smarkets_backall(mock_client, min_profit=0.001)
        assert len(opps) == 0

    def test_none_client(self):
        from scans.smarkets import scan_smarkets_backall

        opps = scan_smarkets_backall(None, min_profit=0.001)
        assert len(opps) == 0


class TestSmarketsBackLayScan:
    def test_finds_crossed_book(self):
        from scans.smarkets import scan_smarkets_backlay

        mock_client = MagicMock()
        mock_client.authenticated = True
        mock_client.fetch_all_markets.return_value = [
            {"id": "mkt1", "name": "Winner", "_event": {"name": "Test Event"}}
        ]
        # Crossed: back at 30%, lay at 50% -> lay_prob > back_prob
        mock_client.list_runners.return_value = [
            {
                "id": "c1",
                "name": "Runner A",
                "quotes": {
                    "best_available_to_back": {"price": 30, "quantity": 50},
                    "best_available_to_lay": {"price": 50, "quantity": 50},
                },
            },
        ]

        opps = scan_smarkets_backlay(mock_client, min_profit=0.001)
        assert len(opps) == 1
        assert opps[0]["type"] == "SmarketsBackLay"
        assert opps[0]["net_profit"] > 0

    def test_skips_normal_book(self):
        from scans.smarkets import scan_smarkets_backlay

        mock_client = MagicMock()
        mock_client.authenticated = True
        mock_client.fetch_all_markets.return_value = [
            {"id": "mkt1", "name": "Winner"}
        ]
        # Normal: back at 50%, lay at 30% -> lay_prob < back_prob
        mock_client.list_runners.return_value = [
            {
                "id": "c1",
                "quotes": {
                    "best_available_to_back": {"price": 50, "quantity": 50},
                    "best_available_to_lay": {"price": 30, "quantity": 50},
                },
            },
        ]

        opps = scan_smarkets_backlay(mock_client, min_profit=0.001)
        assert len(opps) == 0

    def test_not_authenticated(self):
        from scans.smarkets import scan_smarkets_backlay

        mock_client = MagicMock()
        mock_client.authenticated = False

        opps = scan_smarkets_backlay(mock_client, min_profit=0.001)
        assert len(opps) == 0
