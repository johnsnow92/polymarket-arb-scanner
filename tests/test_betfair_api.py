"""Tests for betfair_api.py — Betfair Exchange API client."""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
import betfair_api
from betfair_api import BetfairClient


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Reset betfair circuit breaker state between tests to prevent state bleed."""
    betfair_api._circuit.record_success()
    yield
    betfair_api._circuit.record_success()


@pytest.fixture
def client():
    """Authenticated BetfairClient with mocked session."""
    c = BetfairClient()
    c.session = MagicMock()
    c.api_key = "test_key"
    c.ssoid = "test_ssoid"
    c.authenticated = True
    return c


@pytest.fixture
def raw_client():
    """Unauthenticated BetfairClient with mocked session."""
    c = BetfairClient()
    c.session = MagicMock()
    return c


# ---------------------------------------------------------------------------
# TestBetfairLogin
# ---------------------------------------------------------------------------

class TestBetfairLogin:
    """Login and authentication tests."""

    def test_login_success(self, raw_client):
        """Successful SSO login sets authenticated state."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"status": "SUCCESS", "token": "tok123"}
        raw_client.session.post.return_value = resp

        result = raw_client.login("user", "pass", "key123")

        assert result is True
        assert raw_client.authenticated is True
        assert raw_client.ssoid == "tok123"
        assert raw_client.api_key == "key123"

    def test_login_missing_credentials(self, raw_client):
        """Login fails when credentials are missing and env vars unset."""
        with patch.dict("os.environ", {}, clear=True):
            result = raw_client.login(None, None, None)
        assert result is False
        assert raw_client.authenticated is False

    def test_login_bad_status_in_response(self, raw_client):
        """Login fails when SSO returns non-SUCCESS status."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"status": "FAIL", "error": "INVALID_CREDENTIALS"}
        raw_client.session.post.return_value = resp

        result = raw_client.login("user", "pass", "key")
        assert result is False
        assert raw_client.authenticated is False

    def test_login_non_200_response(self, raw_client):
        """Login fails on non-200 HTTP status."""
        resp = MagicMock(status_code=403)
        raw_client.session.post.return_value = resp

        result = raw_client.login("user", "pass", "key")
        assert result is False

    def test_login_request_exception(self, raw_client):
        """Login fails gracefully on network error."""
        raw_client.session.post.side_effect = requests.RequestException("timeout")

        result = raw_client.login("user", "pass", "key")
        assert result is False
        assert raw_client.authenticated is False

    def test_login_env_var_fallback(self, raw_client):
        """Login uses environment variables when args are None."""
        env = {
            "BETFAIR_USERNAME": "env_user",
            "BETFAIR_PASSWORD": "env_pass",
            "BETFAIR_API_KEY": "env_key",
        }
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"status": "SUCCESS", "token": "env_tok"}
        raw_client.session.post.return_value = resp

        with patch.dict("os.environ", env, clear=True):
            result = raw_client.login()
        assert result is True
        assert raw_client.api_key == "env_key"


# ---------------------------------------------------------------------------
# TestBetfairRequest
# ---------------------------------------------------------------------------

class TestBetfairRequest:
    """Low-level _request method tests."""

    def test_request_returns_none_on_non_200(self, client):
        """Non-200 response returns None."""
        resp = MagicMock(status_code=500, text="Internal Server Error")
        client.session.post.return_value = resp

        result = client._request("listEvents")
        assert result is None

    def test_request_returns_none_on_exception(self, client):
        """Network exception returns None."""
        client.session.post.side_effect = requests.RequestException("boom")

        result = client._request("listEvents")
        assert result is None

    def test_request_success(self, client):
        """200 response returns parsed JSON."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = [{"eventType": {"id": "7"}}]
        client.session.post.return_value = resp

        result = client._request("listEventTypes")
        assert result == [{"eventType": {"id": "7"}}]


# ---------------------------------------------------------------------------
# TestBetfairMarketPrice
# ---------------------------------------------------------------------------

class TestBetfairMarketPrice:
    """Decimal odds to implied probability conversion tests."""

    def test_both_back_and_lay_prices(self, client):
        """Converts back/lay odds to yes/no probabilities."""
        market = {
            "ex": {
                "availableToBack": [{"price": 2.0, "size": 100}],
                "availableToLay": [{"price": 2.5, "size": 50}],
            }
        }
        yes, no = client.get_market_price(market)
        assert yes == pytest.approx(0.5)       # 1/2.0
        assert no == pytest.approx(0.6)        # 1 - 1/2.5

    def test_back_only_derives_no(self, client):
        """When only back price exists, no is derived as 1 - yes."""
        market = {
            "ex": {
                "availableToBack": [{"price": 4.0, "size": 10}],
                "availableToLay": [],
            }
        }
        yes, no = client.get_market_price(market)
        assert yes == pytest.approx(0.25)      # 1/4.0
        assert no == pytest.approx(0.75)       # 1 - 0.25

    def test_lay_only_derives_yes(self, client):
        """When only lay price exists, yes is derived as 1 - no."""
        market = {
            "ex": {
                "availableToBack": [],
                "availableToLay": [{"price": 5.0, "size": 10}],
            }
        }
        yes, no = client.get_market_price(market)
        assert no == pytest.approx(0.8)        # 1 - 1/5.0
        assert yes == pytest.approx(0.2)       # 1 - 0.8

    def test_no_prices_returns_none(self, client):
        """Empty order book returns (None, None)."""
        market = {"ex": {"availableToBack": [], "availableToLay": []}}
        yes, no = client.get_market_price(market)
        assert yes is None
        assert no is None

    def test_no_runners_returns_none(self, client):
        """Market dict without runners or ex key returns (None, None)."""
        yes, no = client.get_market_price({"runners": []})
        assert yes is None
        assert no is None

    def test_market_with_runners_key(self, client):
        """Extracts prices from nested runners list."""
        market = {
            "runners": [{
                "ex": {
                    "availableToBack": [{"price": 3.0, "size": 50}],
                    "availableToLay": [{"price": 3.5, "size": 40}],
                }
            }]
        }
        yes, no = client.get_market_price(market)
        assert yes == pytest.approx(1.0 / 3.0)
        assert no == pytest.approx(1.0 - 1.0 / 3.5)

    def test_zero_price_treated_as_missing(self, client):
        """Back price of 0 is treated as no price available."""
        market = {
            "ex": {
                "availableToBack": [{"price": 0, "size": 10}],
                "availableToLay": [{"price": 2.0, "size": 10}],
            }
        }
        yes, no = client.get_market_price(market)
        assert no == pytest.approx(0.5)        # 1 - 1/2.0
        assert yes == pytest.approx(0.5)       # derived: 1 - 0.5


# ---------------------------------------------------------------------------
# TestBetfairOrders
# ---------------------------------------------------------------------------

class TestBetfairOrders:
    """Order placement, cancellation, and status tests."""

    def test_place_orders_success(self, client):
        """Successful order placement returns response data."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"status": "SUCCESS", "instructionReports": []}
        client.session.post.return_value = resp

        result = client.place_orders("1.234", [{"selectionId": 99}])
        assert result["status"] == "SUCCESS"

    def test_place_orders_failure_status(self, client):
        """Non-SUCCESS status in response returns None."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"status": "FAILURE", "errorCode": "INSUFFICIENT_FUNDS"}
        client.session.post.return_value = resp

        result = client.place_orders("1.234", [{"selectionId": 99}])
        assert result is None

    def test_place_orders_http_error(self, client):
        """Non-200 HTTP response returns None."""
        resp = MagicMock(status_code=400, text="Bad Request")
        client.session.post.return_value = resp

        result = client.place_orders("1.234", [])
        assert result is None

    def test_cancel_orders_success(self, client):
        """Successful cancellation returns True."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"status": "SUCCESS"}
        client.session.post.return_value = resp

        result = client.cancel_orders(market_id="1.234", bet_ids=["B1"])
        assert result is True

    def test_cancel_orders_failure(self, client):
        """Failed cancellation returns False."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"status": "FAILURE"}
        client.session.post.return_value = resp

        result = client.cancel_orders(market_id="1.234")
        assert result is False

    def test_get_order_status_found(self, client):
        """Returns order dict when bet ID is found."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"currentOrders": [{"betId": "B1", "status": "EXECUTABLE"}]}
        client.session.post.return_value = resp

        result = client.get_order_status("B1")
        assert result["betId"] == "B1"

    def test_get_order_status_not_found(self, client):
        """Returns None when no orders match."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"currentOrders": []}
        client.session.post.return_value = resp

        result = client.get_order_status("B999")
        assert result is None

    def test_get_balance_success(self, client):
        """Returns float balance on success."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"availableToBetBalance": 500.75}
        client.session.post.return_value = resp

        result = client.get_balance()
        assert result == pytest.approx(500.75)

    def test_get_current_orders(self, client):
        """Returns list of current orders."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"currentOrders": [{"betId": "B1"}, {"betId": "B2"}]}
        client.session.post.return_value = resp

        result = client.get_current_orders()
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestBetfairFetchData
# ---------------------------------------------------------------------------

class TestBetfairFetchData:
    """Data fetching: events, markets, runners, market books."""

    def test_list_events_with_filter(self, client):
        """list_events passes eventTypeIds filter."""
        with patch.object(client, "_request", return_value=[{"event": {"id": "1"}}]) as mock_req:
            result = client.list_events(event_type_id="7")
        assert len(result) == 1
        mock_req.assert_called_once_with("listEvents", {"eventTypeIds": ["7"]})

    def test_list_events_no_filter(self, client):
        """list_events with no filter passes empty params."""
        with patch.object(client, "_request", return_value=[]) as mock_req:
            client.list_events()
        mock_req.assert_called_once_with("listEvents", {})

    def test_list_markets(self, client):
        """list_markets returns catalogue for an event."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = [{"marketId": "1.234", "marketName": "Winner"}]
        client.session.post.return_value = resp

        result = client.list_markets("12345")
        assert len(result) == 1
        assert result[0]["marketId"] == "1.234"

    def test_list_runners(self, client):
        """list_runners extracts runners from market book."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = [{"runners": [{"selectionId": 1}, {"selectionId": 2}]}]
        client.session.post.return_value = resp

        result = client.list_runners("1.234")
        assert len(result) == 2

    def test_list_market_books_batching(self, client):
        """list_market_books splits >10 IDs into batches."""
        ids = [f"1.{i}" for i in range(15)]
        call_count = [0]

        def mock_post(*args, **kwargs):
            call_count[0] += 1
            resp = MagicMock(status_code=200)
            batch = kwargs.get("json", {}).get("marketIds", [])
            resp.json.return_value = [{"marketId": mid} for mid in batch]
            return resp

        client.session.post.side_effect = mock_post
        result = client.list_market_books(ids)

        assert call_count[0] == 2              # 10 + 5
        assert len(result) == 15


# ---------------------------------------------------------------------------
# TestBetfairAuthGuard
# ---------------------------------------------------------------------------

class TestBetfairAuthGuard:
    """Methods return empty/None when not authenticated."""

    def test_request_requires_auth(self, raw_client):
        """_request returns None without authentication."""
        assert raw_client._request("listEvents") is None

    def test_place_orders_requires_auth(self, raw_client):
        assert raw_client.place_orders("1.234", []) is None

    def test_get_order_status_requires_auth(self, raw_client):
        assert raw_client.get_order_status("B1") is None

    def test_get_balance_requires_auth(self, raw_client):
        assert raw_client.get_balance() is None

    def test_list_markets_requires_auth(self, raw_client):
        assert raw_client.list_markets("12345") == []

    def test_list_runners_requires_auth(self, raw_client):
        assert raw_client.list_runners("1.234") == []

    def test_get_current_orders_requires_auth(self, raw_client):
        assert raw_client.get_current_orders() == []

    def test_cancel_orders_requires_auth(self, raw_client):
        assert raw_client.cancel_orders() is False

    def test_list_market_books_requires_auth(self, raw_client):
        assert raw_client.list_market_books(["1.234"]) == []
