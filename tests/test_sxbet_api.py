"""Tests for sxbet_api.py — SX Bet Exchange API client."""

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sxbet_api import SXBetClient, SXBET_API_URL


@pytest.fixture
def client():
    """Authenticated SXBetClient with mocked session."""
    c = SXBetClient()
    c.api_key = "test_key"
    c.authenticated = True
    c.session = MagicMock()
    return c


# ---------------------------------------------------------------------------
# TestSXBetLogin
# ---------------------------------------------------------------------------

class TestSXBetLogin:
    """Login, env-var fallback, and failure paths."""

    def test_login_success(self):
        c = SXBetClient()
        c.session = MagicMock()
        resp = MagicMock(status_code=200)
        c.session.get.return_value = resp
        assert c.login("my_key") is True
        assert c.authenticated is True
        assert c.wallet_address == "my_key"

    def test_login_env_var_fallback(self):
        c = SXBetClient()
        c.session = MagicMock()
        c.session.get.return_value = MagicMock(status_code=200)
        with patch.dict(os.environ, {"SXBET_API_KEY": "env_key"}):
            assert c.login() is True
        assert c.wallet_address == "env_key"

    def test_login_fails_missing_key(self):
        c = SXBetClient()
        with patch.dict(os.environ, {}, clear=True):
            assert c.login() is False
        assert c.authenticated is False

    def test_login_fails_bad_status(self):
        c = SXBetClient()
        c.session = MagicMock()
        c.session.get.return_value = MagicMock(status_code=401)
        assert c.login("bad_key") is False
        assert c.authenticated is False

    def test_login_fails_request_exception(self):
        c = SXBetClient()
        c.session = MagicMock()
        c.session.get.side_effect = Exception("timeout")
        # requests.RequestException is caught; generic Exception propagates
        import requests as req
        c.session.get.side_effect = req.RequestException("timeout")
        assert c.login("key") is False


# ---------------------------------------------------------------------------
# TestSXBetMarketPrice
# ---------------------------------------------------------------------------

class TestSXBetMarketPrice:
    """get_market_price — extracts YES/NO from order book via _request."""

    def test_prices_from_bids_and_asks(self, client):
        orderbook = {
            "bids": [{"price": "0.65"}],
            "asks": [{"price": "0.70"}],
        }
        with patch.object(client, "_request", return_value=orderbook):
            yes, no = client.get_market_price({"marketHash": "0xabc"})
        assert yes == 0.65
        assert no == pytest.approx(0.30)

    def test_bid_only_infers_no(self, client):
        orderbook = {"bids": [{"price": "0.60"}], "asks": []}
        with patch.object(client, "_request", return_value=orderbook):
            yes, no = client.get_market_price({"marketHash": "0xabc"})
        assert yes == 0.60
        assert no == pytest.approx(0.40)

    def test_ask_only_infers_yes(self, client):
        orderbook = {"bids": [], "asks": [{"price": "0.80"}]}
        with patch.object(client, "_request", return_value=orderbook):
            yes, no = client.get_market_price({"marketHash": "0xabc"})
        assert no == pytest.approx(0.20)
        assert yes == pytest.approx(0.80)

    def test_empty_market_hash_returns_none(self, client):
        yes, no = client.get_market_price({"marketHash": ""})
        assert yes is None
        assert no is None

    def test_orderbook_fetch_fails(self, client):
        with patch.object(client, "_request", return_value=None):
            yes, no = client.get_market_price({"marketHash": "0xabc"})
        assert yes is None
        assert no is None


# ---------------------------------------------------------------------------
# TestSXBetOrders
# ---------------------------------------------------------------------------

class TestSXBetOrders:
    """place_order, get_order_status, cancel_order, get_balance."""

    def test_place_order_success(self, client):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"orderId": "ord1"}
        client.session.post.return_value = resp
        result = client.place_order("0xhash", "out1", "buy", 0.55, 10.0)
        assert result["orderId"] == "ord1"
        # Verify price/size sent as strings
        call_kwargs = client.session.post.call_args
        body = call_kwargs[1]["json"] if "json" in call_kwargs[1] else call_kwargs.kwargs["json"]
        assert body["price"] == "0.55"
        assert body["size"] == "10.0"

    def test_place_order_failure(self, client):
        resp = MagicMock(status_code=400, text="bad request")
        client.session.post.return_value = resp
        result = client.place_order("0xhash", "out1", "buy", 0.55, 10.0)
        assert result is None

    def test_get_order_status(self, client):
        with patch.object(client, "_request", return_value={"status": "filled"}):
            result = client.get_order_status("ord1")
        assert result["status"] == "filled"

    def test_cancel_order_success(self, client):
        client.session.delete.return_value = MagicMock(status_code=200)
        assert client.cancel_order("ord1") is True

    def test_cancel_order_failure(self, client):
        client.session.delete.return_value = MagicMock(status_code=500)
        assert client.cancel_order("ord1") is False

    def test_get_balance_uses_balance_key(self, client):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"balance": "42.5"}
        client.session.get.return_value = resp
        assert client.get_balance() == 42.5

    def test_get_balance_falls_back_to_available(self, client):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"availableBalance": "99.0"}
        client.session.get.return_value = resp
        assert client.get_balance() == 99.0


# ---------------------------------------------------------------------------
# TestSXBetFetchData
# ---------------------------------------------------------------------------

class TestSXBetFetchData:
    """fetch_all_markets, list_runners, get_orderbook, get_market_status."""

    def test_fetch_all_markets_iterates_sports(self, client):
        sports_resp = {"data": [{"sportId": 1}, {"sportId": 2}]}
        markets_s1 = {"data": [{"marketHash": "0xa"}]}
        markets_s2 = {"data": [{"marketHash": "0xb"}, {"marketHash": "0xc"}]}

        def mock_request(method, endpoint, params=None, json_data=None):
            if endpoint == "/sports":
                return sports_resp
            if params and params.get("sportId") == 1:
                return markets_s1
            if params and params.get("sportId") == 2:
                return markets_s2
            return None

        with patch.object(client, "_request", side_effect=mock_request):
            result = client.fetch_all_markets()
        assert len(result) == 3
        assert result[0]["_sport"]["sportId"] == 1

    def test_fetch_all_markets_no_sports(self, client):
        with patch.object(client, "_request", return_value=None):
            assert client.fetch_all_markets() == []

    def test_list_runners(self, client):
        with patch.object(client, "_request", return_value={"data": [{"id": "r1"}]}):
            assert client.list_runners("0xabc") == [{"id": "r1"}]

    def test_list_runners_empty(self, client):
        with patch.object(client, "_request", return_value=None):
            assert client.list_runners("0xabc") == []

    def test_get_orderbook(self, client):
        book = {"bids": [], "asks": []}
        with patch.object(client, "_request", return_value=book):
            assert client.get_orderbook("0xabc") == book

    def test_get_market_status(self, client):
        with patch.object(client, "_request", return_value={"status": "active"}):
            assert client.get_market_status("0xabc")["status"] == "active"


# ---------------------------------------------------------------------------
# TestSXBetAuthGuard
# ---------------------------------------------------------------------------

class TestSXBetAuthGuard:
    """Methods return empty/None when client is not authenticated."""

    def setup_method(self):
        self.client = SXBetClient()  # authenticated = False

    def test_request_returns_none(self):
        assert self.client._request("GET", "/anything") is None

    def test_place_order_returns_none(self):
        assert self.client.place_order("h", "o", "buy", 0.5, 1) is None

    def test_get_balance_returns_none(self):
        assert self.client.get_balance() is None

    def test_get_order_status_returns_none(self):
        assert self.client.get_order_status("ord1") is None

    def test_cancel_order_returns_false(self):
        assert self.client.cancel_order("ord1") is False
