"""Tests for smarkets_api.py — Smarkets Exchange API client."""

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import smarkets_api
from smarkets_api import SmarketsClient, SMARKETS_API_URL


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Reset smarkets circuit breaker state between tests to prevent state bleed."""
    smarkets_api._circuit.record_success()
    yield
    smarkets_api._circuit.record_success()


@pytest.fixture
def client():
    """Authenticated SmarketsClient with a mocked session."""
    c = SmarketsClient()
    c.session = MagicMock()
    c.token = "test_token"
    c.authenticated = True
    return c


@pytest.fixture
def unauth_client():
    """Unauthenticated SmarketsClient."""
    c = SmarketsClient()
    c.session = MagicMock()
    return c


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

class TestSmarketsLogin:
    """Login, auth failures, and env-var fallback."""

    def test_login_success(self):
        """Valid API key + 200 response sets authenticated = True."""
        c = SmarketsClient()
        c.session = MagicMock()
        c.session.get.return_value = MagicMock(status_code=200)
        assert c.login("my_api_key") is True
        assert c.authenticated is True
        assert c.token == "my_api_key"

    def test_login_sets_bearer_header(self):
        """Authorization header uses Bearer scheme."""
        c = SmarketsClient()
        c.session = MagicMock()
        c.session.get.return_value = MagicMock(status_code=200)
        c.login("tok_123")
        c.session.headers.update.assert_called_once()
        headers = c.session.headers.update.call_args[0][0]
        assert headers["Authorization"] == "Bearer tok_123"

    def test_login_fails_missing_key(self):
        """No key and no env var returns False."""
        c = SmarketsClient()
        with patch.dict(os.environ, {}, clear=True):
            assert c.login(None) is False
        assert c.authenticated is False

    def test_login_fails_bad_status(self):
        """Non-200 verification response returns False."""
        c = SmarketsClient()
        c.session = MagicMock()
        c.session.get.return_value = MagicMock(status_code=401)
        assert c.login("bad_key") is False
        assert c.authenticated is False

    def test_login_env_var_fallback(self):
        """Falls back to SMARKETS_API_KEY env var when no arg given."""
        c = SmarketsClient()
        c.session = MagicMock()
        c.session.get.return_value = MagicMock(status_code=200)
        with patch.dict(os.environ, {"SMARKETS_API_KEY": "env_key"}):
            assert c.login() is True
        assert c.token == "env_key"

    def test_login_handles_request_exception(self):
        """Network error during verification returns False."""
        import requests
        c = SmarketsClient()
        c.session = MagicMock()
        c.session.get.side_effect = requests.RequestException("timeout")
        assert c.login("key") is False
        assert c.authenticated is False


# ---------------------------------------------------------------------------
# get_market_price — percentage → probability conversion
# ---------------------------------------------------------------------------

class TestSmarketsMarketPrice:
    """Percentage-to-probability conversion for best back/lay prices."""

    def test_both_prices(self, client):
        """Back 45% → 0.45 yes, lay 55% → 0.45 no."""
        client.session.request.return_value = MagicMock(
            status_code=200,
            json=lambda: {"quotes": [
                {"best_available_to_back": {"price": "45"},
                 "best_available_to_lay": {"price": "55"}},
            ]},
        )
        yes, no = client.get_market_price({"id": "m1"})
        assert yes == pytest.approx(0.45)
        assert no == pytest.approx(0.45)

    def test_back_only_derives_no(self, client):
        """Only back price present: no = 1 - yes."""
        client.session.request.return_value = MagicMock(
            status_code=200,
            json=lambda: {"quotes": [
                {"best_available_to_back": {"price": "60"},
                 "best_available_to_lay": None},
            ]},
        )
        yes, no = client.get_market_price({"id": "m2"})
        assert yes == pytest.approx(0.60)
        assert no == pytest.approx(0.40)

    def test_lay_only_derives_yes(self, client):
        """Only lay price present: yes = 1 - no."""
        client.session.request.return_value = MagicMock(
            status_code=200,
            json=lambda: {"quotes": [
                {"best_available_to_back": None,
                 "best_available_to_lay": {"price": "30"}},
            ]},
        )
        yes, no = client.get_market_price({"id": "m3"})
        assert no == pytest.approx(0.70)
        assert yes == pytest.approx(0.30)

    def test_missing_id_returns_none(self, client):
        """Market dict without id → (None, None)."""
        assert client.get_market_price({}) == (None, None)

    def test_empty_quotes_returns_none(self, client):
        """Empty quotes list → (None, None)."""
        client.session.request.return_value = MagicMock(
            status_code=200,
            json=lambda: {"quotes": []},
        )
        assert client.get_market_price({"id": "m4"}) == (None, None)


# ---------------------------------------------------------------------------
# Orders — place, status, cancel, balance
# ---------------------------------------------------------------------------

class TestSmarketsOrders:
    """Order placement, conversion, status, cancel, and balance."""

    def test_place_order_conversion(self, client):
        """Price → basis points, quantity → cents in JSON body."""
        # PR G: place_order routes through session.request (was session.post)
        # so 429 retries + circuit breaker apply.
        client.session.request.return_value = MagicMock(
            status_code=200,
            json=lambda: {"order_id": "ord1"},
        )
        result = client.place_order("m1", "c1", "buy", 0.55, 10.0)
        assert result == {"order_id": "ord1"}
        body = client.session.request.call_args.kwargs["json"]
        assert body["price"] == "5500"      # 0.55 * 10000
        assert body["quantity"] == "1000"    # 10.0 * 100

    def test_place_order_failure(self, client):
        """Non-200/201 status returns None."""
        client.session.request.return_value = MagicMock(
            status_code=400, text="bad request",
        )
        assert client.place_order("m1", "c1", "buy", 0.5, 5) is None

    def test_get_order_status(self, client):
        """Fetches order by ID."""
        client.session.request.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "ord1", "state": "live"},
        )
        result = client.get_order_status("ord1")
        assert result["state"] == "live"

    def test_cancel_order_success(self, client):
        """204 response → True. PR G: cancel_order routes through
        session.request so circuit breaker applies."""
        client.session.request.return_value = MagicMock(status_code=204)
        assert client.cancel_order("ord1") is True

    def test_cancel_order_failure(self, client):
        """404 response → False."""
        client.session.request.return_value = MagicMock(
            status_code=404, text="not found",
        )
        assert client.cancel_order("ord1") is False

    def test_get_balance(self, client):
        """Balance converts from cents to dollars."""
        client.session.request.return_value = MagicMock(
            status_code=200,
            json=lambda: {"available_balance": "12345"},
        )
        assert client.get_balance() == pytest.approx(123.45)


# ---------------------------------------------------------------------------
# fetch_all_markets, list_runners, get_market_prices, get_market_status
# ---------------------------------------------------------------------------

class TestSmarketsFetchData:
    """Data-fetching endpoints for events, markets, and runners."""

    def test_fetch_all_markets(self, client):
        """Events with markets are flattened into a list."""
        events_resp = MagicMock(status_code=200, json=lambda: {
            "events": [{"id": "e1", "name": "Election"}],
        })
        markets_resp = MagicMock(status_code=200, json=lambda: {
            "markets": [{"id": "m1", "name": "Winner"}],
        })
        client.session.request.side_effect = [events_resp, markets_resp]
        result = client.fetch_all_markets()
        assert len(result) == 1
        assert result[0]["id"] == "m1"
        assert result[0]["_event"]["id"] == "e1"

    def test_fetch_all_markets_empty(self, client):
        """No events → empty list."""
        client.session.request.return_value = MagicMock(
            status_code=200, json=lambda: {"events": []},
        )
        assert client.fetch_all_markets() == []

    def test_list_runners(self, client):
        """Returns contracts list for a market."""
        client.session.request.return_value = MagicMock(
            status_code=200,
            json=lambda: {"contracts": [{"id": "c1"}, {"id": "c2"}]},
        )
        runners = client.list_runners("m1")
        assert len(runners) == 2

    def test_get_market_prices(self, client):
        """Returns raw quotes dict."""
        client.session.request.return_value = MagicMock(
            status_code=200,
            json=lambda: {"quotes": [{"price": "50"}]},
        )
        data = client.get_market_prices("m1")
        assert "quotes" in data

    def test_get_market_status(self, client):
        """Returns market state info."""
        client.session.request.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "m1", "state": "live"},
        )
        result = client.get_market_status("m1")
        assert result["state"] == "live"


# ---------------------------------------------------------------------------
# Auth guard — unauthenticated calls
# ---------------------------------------------------------------------------

class TestSmarketsAuthGuard:
    """Methods return empty / None when not authenticated."""

    def test_place_order_blocked(self, unauth_client):
        assert unauth_client.place_order("m", "c", "buy", 0.5, 1) is None

    def test_get_balance_blocked(self, unauth_client):
        assert unauth_client.get_balance() is None

    def test_get_order_status_blocked(self, unauth_client):
        assert unauth_client.get_order_status("ord1") is None

    def test_cancel_order_blocked(self, unauth_client):
        assert unauth_client.cancel_order("ord1") is False

    def test_fetch_markets_blocked(self, unauth_client):
        """_request returns None → fetch_all_markets returns []."""
        assert unauth_client.fetch_all_markets() == []
