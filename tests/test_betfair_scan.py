"""Tests for scans/betfair.py — Betfair standalone arbitrage scans."""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fees import net_profit_betfair_backall, net_profit_betfair_backlay


class TestBetfairFees:
    def test_backall_profitable(self):
        """Under-round book: sum of implied probs < 1.0."""
        probs = [0.30, 0.30, 0.30]  # Total = 0.90
        result = net_profit_betfair_backall(probs, commission_rate=0.05)
        assert result["gross_spread"] == pytest.approx(0.10)
        assert result["net_profit"] > 0
        # Fee = 5% of (1.0 - 0.30) = 0.035
        assert result["fees"] == pytest.approx(0.035)

    def test_backall_unprofitable(self):
        """Over-round book: sum > 1.0."""
        probs = [0.40, 0.40, 0.30]
        result = net_profit_betfair_backall(probs, commission_rate=0.05)
        assert result["net_profit"] <= 0

    def test_backall_exact_round(self):
        """Exactly 1.0 = no profit."""
        probs = [0.50, 0.30, 0.20]
        result = net_profit_betfair_backall(probs, commission_rate=0.05)
        assert result["net_profit"] <= 0

    def test_backlay_profitable(self):
        """Crossed book: back_prob < lay_prob."""
        result = net_profit_betfair_backlay(0.40, 0.50, commission_rate=0.05)
        assert result["gross_spread"] == pytest.approx(0.10)
        # Fee = 5% of 0.10 = 0.005
        assert result["fees"] == pytest.approx(0.005)
        assert result["net_profit"] == pytest.approx(0.095)

    def test_backlay_not_crossed(self):
        """Normal book: lay_prob <= back_prob."""
        result = net_profit_betfair_backlay(0.50, 0.40, commission_rate=0.05)
        assert result["net_profit"] == 0

    def test_backlay_zero_commission(self):
        """With 0% commission, full spread is profit."""
        result = net_profit_betfair_backlay(0.40, 0.50, commission_rate=0.0)
        assert result["net_profit"] == pytest.approx(0.10)

    def test_backall_uses_cheapest_for_fee(self):
        """Fee is calculated on the cheapest (highest profit) runner."""
        probs = [0.10, 0.30, 0.40]  # Total = 0.80, cheapest = 0.10
        result = net_profit_betfair_backall(probs, commission_rate=0.05)
        # Fee = 5% of (1.0 - 0.10) = 0.045
        assert result["fees"] == pytest.approx(0.045)


class TestBetfairBackAllScan:
    def test_finds_underround_book(self):
        from scans.betfair import scan_betfair_backall

        mock_client = MagicMock()
        mock_client.authenticated = True
        mock_client.list_events.return_value = [
            {"event": {"id": "evt1", "name": "Test Event"}}
        ]
        mock_client.list_markets.return_value = [
            {"marketId": "mkt1", "marketName": "Winner", "event": {"name": "Test"},
             "runners": [
                 {"selectionId": 1, "runnerName": "Runner A"},
                 {"selectionId": 2, "runnerName": "Runner B"},
             ]}
        ]
        # Under-round book: back odds 4.0 and 4.0 -> implied 0.25 + 0.25 = 0.50
        mock_client.list_market_books.return_value = [
            {"marketId": "mkt1", "runners": [
                {"selectionId": 1, "ex": {
                    "availableToBack": [{"price": 4.0, "size": 100}],
                    "availableToLay": [],
                }},
                {"selectionId": 2, "ex": {
                    "availableToBack": [{"price": 4.0, "size": 100}],
                    "availableToLay": [],
                }},
            ]}
        ]

        opps = scan_betfair_backall(mock_client, min_profit=0.001)
        assert len(opps) == 1
        assert opps[0]["type"] == "BetfairBackAll"

    def test_skips_overround_book(self):
        from scans.betfair import scan_betfair_backall

        mock_client = MagicMock()
        mock_client.authenticated = True
        mock_client.list_events.return_value = [
            {"event": {"id": "evt1", "name": "Test"}}
        ]
        mock_client.list_markets.return_value = [
            {"marketId": "mkt1", "marketName": "Winner",
             "runners": [{"selectionId": 1}, {"selectionId": 2}]}
        ]
        # Over-round: odds 1.5 and 1.5 -> implied 0.667 + 0.667 = 1.33
        mock_client.list_market_books.return_value = [
            {"marketId": "mkt1", "runners": [
                {"selectionId": 1, "ex": {
                    "availableToBack": [{"price": 1.5, "size": 100}],
                }},
                {"selectionId": 2, "ex": {
                    "availableToBack": [{"price": 1.5, "size": 100}],
                }},
            ]}
        ]

        opps = scan_betfair_backall(mock_client, min_profit=0.001)
        assert len(opps) == 0


class TestBetfairBackLayScan:
    def test_finds_crossed_book(self):
        from scans.betfair import scan_betfair_backlay

        mock_client = MagicMock()
        mock_client.authenticated = True
        mock_client.list_events.return_value = [
            {"event": {"id": "evt1", "name": "Test Event"}}
        ]
        mock_client.list_markets.return_value = [
            {"marketId": "mkt1", "marketName": "Winner",
             "runners": [{"selectionId": 1, "runnerName": "Runner A"}]}
        ]
        # Crossed: back at 3.0, lay at 2.0 -> back_odds > lay_odds
        # back_prob = 1/3.0 = 0.333, lay_prob = 1/2.0 = 0.50
        # lay_prob > back_prob -> profit
        mock_client.list_market_books.return_value = [
            {"marketId": "mkt1", "runners": [
                {"selectionId": 1, "ex": {
                    "availableToBack": [{"price": 3.0, "size": 50}],
                    "availableToLay": [{"price": 2.0, "size": 50}],
                }},
            ]}
        ]

        opps = scan_betfair_backlay(mock_client, min_profit=0.001)
        assert len(opps) == 1
        assert opps[0]["type"] == "BetfairBackLay"

    def test_not_authenticated(self):
        from scans.betfair import scan_betfair_backlay

        mock_client = MagicMock()
        mock_client.authenticated = False

        opps = scan_betfair_backlay(mock_client, min_profit=0.001)
        assert len(opps) == 0
