"""Tests for gemini_api.py — Gemini Predictions API client."""

import base64
import hashlib
import hmac
import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gemini_api import GeminiClient


@pytest.fixture
def client():
    c = GeminiClient()
    c.api_key = "test_key"
    c.api_secret = "test_secret"
    c.authenticated = True
    return c


# ---------------------------------------------------------------------------
# Auth / signing
# ---------------------------------------------------------------------------

class TestGeminiAuth:
    def test_sign_request_produces_correct_headers(self, client):
        headers = client._sign_request("/v1/balances")
        assert "X-GEMINI-APIKEY" in headers
        assert headers["X-GEMINI-APIKEY"] == "test_key"
        assert "X-GEMINI-PAYLOAD" in headers
        assert "X-GEMINI-SIGNATURE" in headers

        # Verify payload is valid base64 JSON with request + nonce
        payload_b64 = headers["X-GEMINI-PAYLOAD"]
        payload_json = base64.b64decode(payload_b64)
        payload = json.loads(payload_json)
        assert payload["request"] == "/v1/balances"
        assert "nonce" in payload

    def test_sign_request_hmac_sha384(self, client):
        headers = client._sign_request("/v1/balances")
        payload_b64 = headers["X-GEMINI-PAYLOAD"].encode("utf-8")
        expected_sig = hmac.new(
            b"test_secret", payload_b64, hashlib.sha384
        ).hexdigest()
        assert headers["X-GEMINI-SIGNATURE"] == expected_sig

    def test_sign_request_includes_extra_payload(self, client):
        headers = client._sign_request("/v1/order", payload_data={"symbol": "TEST"})
        payload_b64 = headers["X-GEMINI-PAYLOAD"]
        payload = json.loads(base64.b64decode(payload_b64))
        assert payload["symbol"] == "TEST"
        assert payload["request"] == "/v1/order"

    def test_login_fails_without_credentials(self):
        c = GeminiClient()
        with patch.dict(os.environ, {}, clear=True):
            result = c.login(None, None)
        assert result is False
        assert c.authenticated is False

    def test_login_succeeds_with_valid_balance(self):
        c = GeminiClient()
        # Login now verifies via _private_request("/v1/balances") returning a list
        with patch.object(c, "_private_request", return_value=[{"currency": "USD", "amount": "100.0"}]):
            result = c.login("key", "secret")
        assert result is True
        assert c.authenticated is True

    def test_login_fails_on_balance_none(self):
        c = GeminiClient()
        with patch.object(c, "_private_request", return_value=None):
            result = c.login("key", "secret")
        assert result is False

    def test_login_succeeds_with_empty_balance(self):
        """Empty list (unfunded account) is still valid auth."""
        c = GeminiClient()
        with patch.object(c, "_private_request", return_value=[]):
            result = c.login("key", "secret")
        assert result is True

    def test_login_sets_account_for_master_key(self):
        c = GeminiClient()
        with patch.object(c, "_private_request", return_value=[]):
            c.login("master-abc123", "secret")
        assert c._account == "primary"


# ---------------------------------------------------------------------------
# fetch_all_markets
# ---------------------------------------------------------------------------

class TestFetchAllMarkets:
    def test_fetches_and_normalizes_events(self, client):
        mock_events = [
            {
                "eventTicker": "EVT1",
                "title": "Will Bitcoin hit $100k?",
                "category": "crypto",
                "contracts": [
                    {"id": "c1", "label": "Yes", "price": 0.65, "instrumentSymbol": "BTC100K-YES"},
                    {"id": "c2", "label": "No", "price": 0.35, "instrumentSymbol": "BTC100K-NO"},
                ],
            }
        ]
        with patch.object(client, "_public_request", return_value=mock_events):
            result = client.fetch_all_markets()

        assert len(result) == 1
        event = result[0]
        assert event["id"] == "EVT1"
        assert event["title"] == "Will Bitcoin hit $100k?"
        assert event["type"] == "binary"
        assert len(event["contracts"]) == 2

    def test_pagination_stops_on_empty(self, client):
        calls = [0]
        def mock_public(endpoint, params=None):
            calls[0] += 1
            if calls[0] == 1:
                return [{"eventTicker": "E1", "title": "T1", "contracts": [
                    {"id": "c1", "label": "Yes", "price": 0.5, "instrumentSymbol": "S1"},
                    {"id": "c2", "label": "No", "price": 0.5, "instrumentSymbol": "S2"},
                ]}]
            return []

        with patch.object(client, "_public_request", side_effect=mock_public):
            result = client.fetch_all_markets()
        assert len(result) == 1

    def test_categorical_event_type(self, client):
        mock_events = [{
            "eventTicker": "EVT2", "title": "Who wins?",
            "contracts": [
                {"id": "c1", "label": "Alice", "price": 0.3, "instrumentSymbol": "S1"},
                {"id": "c2", "label": "Bob", "price": 0.4, "instrumentSymbol": "S2"},
                {"id": "c3", "label": "Carol", "price": 0.3, "instrumentSymbol": "S3"},
            ],
        }]
        with patch.object(client, "_public_request", return_value=mock_events):
            result = client.fetch_all_markets()
        assert result[0]["type"] == "categorical"


# ---------------------------------------------------------------------------
# get_market_price
# ---------------------------------------------------------------------------

class TestGetMarketPrice:
    def test_extracts_yes_no_prices(self, client):
        event = {
            "contracts": [
                {"label": "Yes", "price": 0.65},
                {"label": "No", "price": 0.35},
            ]
        }
        yes, no = client.get_market_price(event)
        assert yes == 0.65
        assert no == 0.35

    def test_returns_none_for_non_binary(self, client):
        event = {"contracts": [{"label": "A", "price": 0.5}]}
        yes, no = client.get_market_price(event)
        assert yes is None
        assert no is None


# ---------------------------------------------------------------------------
# place_order
# ---------------------------------------------------------------------------

class TestPlaceOrder:
    def test_success(self, client):
        mock_resp = {"orderId": "123", "status": "accepted"}
        with patch.object(client, "_private_request", return_value=mock_resp):
            result = client.place_order("SYM", "buy", "yes", 10, 0.50)
        assert result["orderId"] == "123"

    def test_fails_when_not_authenticated(self):
        c = GeminiClient()
        c.authenticated = False
        result = c.place_order("SYM", "buy", "yes", 10, 0.50)
        assert result is None


# ---------------------------------------------------------------------------
# get_order_status
# ---------------------------------------------------------------------------

class TestGetOrderStatus:
    def test_finds_in_active_orders(self, client):
        active = [{"orderId": "123", "status": "live"}]
        with patch.object(client, "_private_request", side_effect=[active, []]):
            result = client.get_order_status("123")
        assert result["status"] == "live"

    def test_falls_back_to_history(self, client):
        history = [{"orderId": "456", "status": "filled"}]
        with patch.object(client, "_private_request", side_effect=[[], history]):
            result = client.get_order_status("456")
        assert result["status"] == "filled"

    def test_returns_none_when_not_found(self, client):
        with patch.object(client, "_private_request", side_effect=[[], []]):
            result = client.get_order_status("999")
        assert result is None


# ---------------------------------------------------------------------------
# get_balance
# ---------------------------------------------------------------------------

class TestGetBalance:
    def test_returns_usd_balance(self, client):
        data = [{"currency": "USD", "available": "1234.56"}]
        with patch.object(client, "_private_request", return_value=data):
            result = client.get_balance()
        assert result == 1234.56

    def test_returns_none_on_failure(self, client):
        with patch.object(client, "_private_request", return_value=None):
            result = client.get_balance()
        assert result is None
