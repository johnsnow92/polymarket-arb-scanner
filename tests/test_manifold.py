"""Tests for manifold_api.py — Manifold Markets API client."""

import pytest
from unittest.mock import MagicMock, patch

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from manifold_api import ManifoldClient


@pytest.fixture
def client():
    return ManifoldClient(api_key="test_key")


# ---------------------------------------------------------------------------
# Sweepstakes-only filter
# ---------------------------------------------------------------------------

class TestSweepstakesFilter:
    def test_sweepstakes_only_filters_mana_markets(self, client):
        """Default fetch_markets should only return sweepstakes (CASH) markets."""
        mock_markets = [
            {"id": "1", "question": "Play money market", "token": "MANA"},
            {"id": "2", "question": "Real money market", "token": "CASH"},
            {"id": "3", "question": "Another play money", "token": "MANA"},
            {"id": "4", "question": "Sweepstakes market", "token": "CASH"},
        ]
        with patch.object(client.session, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_markets
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            result = client.fetch_markets(limit=100, sweepstakes_only=True)
            assert len(result) == 2
            assert all(m["token"] == "CASH" for m in result)

    def test_sweepstakes_only_default_behavior(self, client):
        """Default behavior should exclude play-money markets."""
        mock_markets = [
            {"id": "1", "question": "Mana market", "token": "MANA"},
            {"id": "2", "question": "Cash market", "token": "CASH"},
        ]
        with patch.object(client.session, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_markets
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            # Default: sweepstakes_only=True
            result = client.fetch_markets()
            assert len(result) == 1
            assert result[0]["token"] == "CASH"

    def test_sweepstakes_disabled_returns_all(self, client):
        """When sweepstakes_only=False, all markets should be returned."""
        mock_markets = [
            {"id": "1", "question": "Mana market", "token": "MANA"},
            {"id": "2", "question": "Cash market", "token": "CASH"},
        ]
        with patch.object(client.session, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_markets
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            result = client.fetch_markets(sweepstakes_only=False)
            assert len(result) == 2

    def test_sweepstakes_filter_handles_missing_token(self, client):
        """Markets without a 'token' field should be excluded when filtering."""
        mock_markets = [
            {"id": "1", "question": "No token field"},
            {"id": "2", "question": "Cash market", "token": "CASH"},
        ]
        with patch.object(client.session, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_markets
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            result = client.fetch_markets(sweepstakes_only=True)
            assert len(result) == 1
            assert result[0]["token"] == "CASH"

    def test_sweepstakes_filter_empty_list(self, client):
        """Empty market list should return empty."""
        with patch.object(client.session, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = []
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            result = client.fetch_markets(sweepstakes_only=True)
            assert result == []
