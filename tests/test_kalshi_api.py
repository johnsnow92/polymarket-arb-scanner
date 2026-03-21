"""Tests for kalshi_api.py — Kalshi API client with RSA-PSS authentication."""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kalshi_api
from kalshi_api import KalshiClient, _rate_limit, _RateLimitError, KALSHI_BASE_URL, KALSHI_API_PATH


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Reset kalshi circuit breaker state between tests to prevent state bleed."""
    kalshi_api._circuit.record_success()
    yield
    kalshi_api._circuit.record_success()


@pytest.fixture
def client():
    """Return a KalshiClient with mocked session and fake key material."""
    c = KalshiClient()
    c.session = MagicMock()
    c.api_key_id = "test-key-id"
    c.private_key = MagicMock()
    # _sign_pss calls private_key.sign() — return deterministic bytes
    c.private_key.sign.return_value = b"fakesignature"
    return c


def _mock_response(status_code=200, json_data=None, text=""):
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# TestKalshiLogin
# ---------------------------------------------------------------------------

class TestKalshiLogin:
    """Login / authentication flows."""

    def test_login_with_pem_file(self, tmp_path):
        """login_with_api_key loads key from PEM file path."""
        c = KalshiClient()
        c.session = MagicMock()
        fake_key = MagicMock()
        fake_key.sign.return_value = b"sig"
        ok_resp = _mock_response(200)
        c.session.request.return_value = ok_resp
        with patch("kalshi_api._load_private_key", return_value=fake_key) as lp:
            result = c.login_with_api_key("kid", private_key_path="/tmp/key.pem")
        lp.assert_called_once_with("/tmp/key.pem")
        assert result is True

    def test_login_with_base64_key(self):
        """login_with_api_key loads key from base64 string."""
        c = KalshiClient()
        c.session = MagicMock()
        fake_key = MagicMock()
        fake_key.sign.return_value = b"sig"
        ok_resp = _mock_response(200)
        c.session.request.return_value = ok_resp
        with patch("kalshi_api._load_private_key_from_base64", return_value=fake_key) as lb:
            result = c.login_with_api_key("kid", private_key_base64="AAAA")
        lb.assert_called_once_with("AAAA")
        assert result is True

    def test_login_fails_no_key_provided(self):
        """Returns False when neither path nor base64 given."""
        c = KalshiClient()
        result = c.login_with_api_key("kid")
        assert result is False

    def test_login_fails_bad_key_file(self):
        """Returns False when PEM file not found."""
        c = KalshiClient()
        with patch("kalshi_api._load_private_key", side_effect=FileNotFoundError):
            result = c.login_with_api_key("kid", private_key_path="/no/such.pem")
        assert result is False

    def test_login_fails_auth_check_non_200(self):
        """Returns False when /exchange/status returns non-200."""
        c = KalshiClient()
        c.session = MagicMock()
        fake_key = MagicMock()
        fake_key.sign.return_value = b"sig"
        c.session.request.return_value = _mock_response(403)
        with patch("kalshi_api._load_private_key", return_value=fake_key):
            result = c.login_with_api_key("kid", private_key_path="/tmp/k.pem")
        assert result is False

    def test_login_fails_auth_check_none_response(self):
        """Returns False when _request returns None."""
        c = KalshiClient()
        c.session = MagicMock()
        fake_key = MagicMock()
        fake_key.sign.return_value = b"sig"
        import requests as _req
        c.session.request.side_effect = _req.RequestException("fail")
        with patch("kalshi_api._load_private_key", return_value=fake_key):
            result = c.login_with_api_key("kid", private_key_path="/tmp/k.pem")
        assert result is False


# ---------------------------------------------------------------------------
# TestKalshiRateLimit
# ---------------------------------------------------------------------------

class TestKalshiRateLimit:
    """Rate-limiting behaviour."""

    def test_rate_limit_sleeps_when_called_fast(self):
        """_rate_limit sleeps if called faster than KALSHI_RATE_LIMIT."""
        import kalshi_api
        kalshi_api._last_request_time = time.time()  # "just called"
        with patch("kalshi_api.time.sleep") as mock_sleep:
            _rate_limit()
        # Should have slept some positive amount
        assert mock_sleep.called
        slept = mock_sleep.call_args[0][0]
        assert slept > 0

    def test_rate_limit_no_sleep_when_enough_gap(self):
        """No sleep when enough time has passed."""
        import kalshi_api
        kalshi_api._last_request_time = time.time() - 10
        with patch("kalshi_api.time.sleep") as mock_sleep:
            _rate_limit()
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# TestKalshiRequest
# ---------------------------------------------------------------------------

class TestKalshiRequest:
    """Low-level _request method."""

    def test_builds_correct_url_and_headers(self, client):
        """URL is base + api_path + path; auth headers are set."""
        client.session.request.return_value = _mock_response(200)
        resp = client._request("GET", "/markets")
        call_args = client.session.request.call_args
        assert call_args[0][0] == "GET"
        assert call_args[0][1] == KALSHI_BASE_URL + KALSHI_API_PATH + "/markets"
        headers = call_args[1]["headers"]
        assert "KALSHI-ACCESS-KEY" in headers
        assert headers["KALSHI-ACCESS-KEY"] == "test-key-id"

    @patch("kalshi_api._rate_limit")
    def test_retry_on_429(self, mock_rl, client):
        """HTTP 429 raises _RateLimitError (tenacity retries)."""
        client.session.request.return_value = _mock_response(429)
        with pytest.raises(_RateLimitError):
            client._request("GET", "/markets")
        # tenacity retried 3 times
        assert client.session.request.call_count == 3

    @patch("kalshi_api._rate_limit")
    def test_retry_on_connection_error(self, mock_rl, client):
        """ConnectionError is retried then re-raised."""
        import requests as _req
        client.session.request.side_effect = _req.ConnectionError("down")
        with pytest.raises(_req.ConnectionError):
            client._request("GET", "/markets")
        assert client.session.request.call_count == 3

    @patch("kalshi_api._rate_limit")
    def test_returns_none_on_generic_request_exception(self, mock_rl, client):
        """Non-retryable RequestException returns None."""
        import requests as _req
        client.session.request.side_effect = _req.RequestException("weird")
        result = client._request("GET", "/markets")
        assert result is None


# ---------------------------------------------------------------------------
# TestKalshiMarketPrice
# ---------------------------------------------------------------------------

class TestKalshiMarketPrice:
    """get_market_price pure-logic tests."""

    def test_dollar_fields_preferred(self, client):
        """Uses yes_ask_dollars / no_ask_dollars when present."""
        market = {"yes_ask_dollars": 0.62, "no_ask_dollars": 0.40}
        yes, no = client.get_market_price(market)
        assert yes == 0.62
        assert no == 0.40

    def test_cent_fallback(self, client):
        """Falls back to yes_ask/no_ask cent fields divided by 100."""
        market = {"yes_ask": 55, "no_ask": 47}
        yes, no = client.get_market_price(market)
        assert yes == pytest.approx(0.55)
        assert no == pytest.approx(0.47)

    def test_returns_none_when_missing(self, client):
        """Returns (None, None) when no price fields exist."""
        yes, no = client.get_market_price({})
        assert yes is None
        assert no is None

    def test_dollar_zero_falls_through_to_cents(self, client):
        """Dollar fields of 0 are invalid — falls to cent fields."""
        market = {"yes_ask_dollars": 0, "no_ask_dollars": 0.40, "yes_ask": 30, "no_ask": 70}
        yes, no = client.get_market_price(market)
        assert yes == pytest.approx(0.30)
        assert no == pytest.approx(0.70)

    def test_dollar_fields_invalid_type(self, client):
        """Non-numeric dollar fields fall through to cents."""
        market = {"yes_ask_dollars": "bad", "no_ask_dollars": 0.5, "yes_ask": 20, "no_ask": 80}
        yes, no = client.get_market_price(market)
        assert yes == pytest.approx(0.20)
        assert no == pytest.approx(0.80)

    def test_partial_cent_fields_return_none(self, client):
        """If only one cent field present, returns (None, None)."""
        market = {"yes_ask": 40}
        yes, no = client.get_market_price(market)
        assert yes is None
        assert no is None


# ---------------------------------------------------------------------------
# TestKalshiOrders
# ---------------------------------------------------------------------------

class TestKalshiOrders:
    """Order placement, status, cancel, balance, positions."""

    @patch("kalshi_api._rate_limit")
    def test_place_order_success(self, mock_rl, client):
        """place_order returns parsed JSON on 200."""
        client.session.request.return_value = _mock_response(
            200, {"order": {"id": "o1", "status": "resting"}}
        )
        result = client.place_order("TICK", "yes", "buy", 5, 0.60)
        assert result == {"order": {"id": "o1", "status": "resting"}}
        body = client.session.request.call_args[1]["json"]
        assert body["yes_price"] == 60
        assert "no_price" not in body

    @patch("kalshi_api._rate_limit")
    def test_place_order_no_side_price(self, mock_rl, client):
        """Placing a 'no' order sets no_price instead of yes_price."""
        client.session.request.return_value = _mock_response(201, {"order": {"id": "o2"}})
        result = client.place_order("TICK", "no", "buy", 3, 0.35)
        body = client.session.request.call_args[1]["json"]
        assert body["no_price"] == 35
        assert "yes_price" not in body

    @patch("kalshi_api._rate_limit")
    def test_place_order_returns_none_on_400(self, mock_rl, client):
        """Non-success HTTP returns None."""
        client.session.request.return_value = _mock_response(400, text="bad request")
        result = client.place_order("TICK", "yes", "buy", 1, 0.50)
        assert result is None

    @patch("kalshi_api._rate_limit")
    def test_place_order_returns_none_on_exception(self, mock_rl, client):
        """Exception during request returns None."""
        import requests as _req
        client.session.request.side_effect = _req.ConnectionError("down")
        result = client.place_order("TICK", "yes", "buy", 1, 0.50)
        assert result is None

    @patch("kalshi_api._rate_limit")
    def test_get_order_status_success(self, mock_rl, client):
        """get_order_status returns order dict."""
        client.session.request.return_value = _mock_response(
            200, {"order": {"order_id": "o1", "status": "filled"}}
        )
        result = client.get_order_status("o1")
        assert result["status"] == "filled"

    @patch("kalshi_api._rate_limit")
    def test_get_order_status_returns_none_on_failure(self, mock_rl, client):
        """get_order_status returns None on non-200."""
        client.session.request.return_value = _mock_response(404)
        result = client.get_order_status("o1")
        assert result is None

    @patch("kalshi_api._rate_limit")
    def test_cancel_order_success(self, mock_rl, client):
        """cancel_order returns True on 200/204."""
        client.session.request.return_value = _mock_response(204)
        assert client.cancel_order("o1") is True

    @patch("kalshi_api._rate_limit")
    def test_cancel_order_failure(self, mock_rl, client):
        """cancel_order returns False on error status."""
        client.session.request.return_value = _mock_response(400)
        assert client.cancel_order("o1") is False

    @patch("kalshi_api._rate_limit")
    def test_get_balance_converts_cents_to_dollars(self, mock_rl, client):
        """get_balance divides cents by 100."""
        client.session.request.return_value = _mock_response(200, {"balance": 5432})
        result = client.get_balance()
        assert result == pytest.approx(54.32)

    @patch("kalshi_api._rate_limit")
    def test_get_balance_returns_none_on_failure(self, mock_rl, client):
        """get_balance returns None on non-200."""
        client.session.request.return_value = _mock_response(500)
        assert client.get_balance() is None

    @patch("kalshi_api._rate_limit")
    def test_get_positions_success(self, mock_rl, client):
        """get_positions returns market_positions list."""
        positions = [{"ticker": "T1", "position": 10}]
        client.session.request.return_value = _mock_response(200, {"market_positions": positions})
        assert client.get_positions() == positions

    @patch("kalshi_api._rate_limit")
    def test_get_positions_returns_empty_on_failure(self, mock_rl, client):
        """get_positions returns [] on error."""
        client.session.request.return_value = _mock_response(500)
        assert client.get_positions() == []


# ---------------------------------------------------------------------------
# TestKalshiFetchData
# ---------------------------------------------------------------------------

class TestKalshiFetchData:
    """Event/market/order-book fetching."""

    @patch("kalshi_api._rate_limit")
    def test_fetch_all_events_single_page(self, mock_rl, client):
        """Single page of events with no cursor returns all."""
        client.session.request.return_value = _mock_response(200, {
            "events": [{"event_ticker": "E1"}, {"event_ticker": "E2"}],
            "cursor": "",
        })
        result = client.fetch_all_events()
        assert len(result) == 2

    @patch("kalshi_api._rate_limit")
    def test_fetch_all_events_pagination(self, mock_rl, client):
        """Multiple pages are fetched until empty cursor."""
        page1 = _mock_response(200, {"events": [{"event_ticker": "E1"}], "cursor": "abc"})
        page2 = _mock_response(200, {"events": [{"event_ticker": "E2"}], "cursor": ""})
        client.session.request.side_effect = [page1, page2]
        result = client.fetch_all_events()
        assert len(result) == 2
        assert client.session.request.call_count == 2

    @patch("kalshi_api._rate_limit")
    def test_fetch_all_events_stops_on_error(self, mock_rl, client):
        """Stops pagination on non-200 response."""
        client.session.request.return_value = _mock_response(500)
        result = client.fetch_all_events()
        assert result == []

    @patch("kalshi_api._rate_limit")
    def test_fetch_markets_for_event_success(self, mock_rl, client):
        """Returns markets list for given event."""
        markets = [{"ticker": "M1"}, {"ticker": "M2"}]
        client.session.request.return_value = _mock_response(200, {"markets": markets})
        result = client.fetch_markets_for_event("EVT1")
        assert len(result) == 2

    @patch("kalshi_api._rate_limit")
    def test_fetch_markets_for_event_returns_empty_on_error(self, mock_rl, client):
        """Returns [] on non-200."""
        client.session.request.return_value = _mock_response(404)
        assert client.fetch_markets_for_event("EVT1") == []

    @patch("kalshi_api._rate_limit")
    def test_fetch_order_book_success(self, mock_rl, client):
        """Returns parsed order-book JSON on 200."""
        book = {"orderbook": {"yes": [[55, 100]], "no": [[45, 200]]}}
        client.session.request.return_value = _mock_response(200, book)
        result = client.fetch_order_book("TICK")
        assert result == book

    @patch("kalshi_api._rate_limit")
    def test_fetch_order_book_returns_none_on_error(self, mock_rl, client):
        """Returns None on non-200."""
        client.session.request.return_value = _mock_response(500)
        assert client.fetch_order_book("TICK") is None


# ---------------------------------------------------------------------------
# TestKalshiOrderBookDepth
# ---------------------------------------------------------------------------

class TestKalshiOrderBookDepth:
    """get_order_book_depth with various entry formats."""

    def test_list_entries(self, client):
        """Entries as [price, quantity] lists."""
        book = {"orderbook": {"yes": [[55, 120]], "no": [[45, 80]]}}
        with patch.object(client, "fetch_order_book", return_value=book):
            result = client.get_order_book_depth("TICK")
        assert result["yes_ask_size"] == 120
        assert result["no_ask_size"] == 80

    def test_dict_entries_quantity_key(self, client):
        """Entries as dicts with 'quantity' key."""
        book = {"orderbook": {"yes": [{"price": 55, "quantity": 50}], "no": [{"price": 45, "quantity": 30}]}}
        with patch.object(client, "fetch_order_book", return_value=book):
            result = client.get_order_book_depth("TICK")
        assert result["yes_ask_size"] == 50
        assert result["no_ask_size"] == 30

    def test_dict_entries_size_key(self, client):
        """Entries as dicts with 'size' key (fallback)."""
        book = {"orderbook": {"yes": [{"price": 55, "size": 25}], "no": [{"price": 45, "size": 15}]}}
        with patch.object(client, "fetch_order_book", return_value=book):
            result = client.get_order_book_depth("TICK")
        assert result["yes_ask_size"] == 25
        assert result["no_ask_size"] == 15

    def test_empty_book(self, client):
        """Empty order book sides return 0 sizes."""
        book = {"orderbook": {"yes": [], "no": []}}
        with patch.object(client, "fetch_order_book", return_value=book):
            result = client.get_order_book_depth("TICK")
        assert result["yes_ask_size"] == 0
        assert result["no_ask_size"] == 0

    def test_returns_none_when_no_book(self, client):
        """Returns None when fetch_order_book returns None."""
        with patch.object(client, "fetch_order_book", return_value=None):
            result = client.get_order_book_depth("TICK")
        assert result is None

    def test_top_level_keys_no_orderbook_wrapper(self, client):
        """Handles book where yes/no are at top level (no 'orderbook' key)."""
        book = {"yes": [[60, 200]], "no": [[40, 150]]}
        with patch.object(client, "fetch_order_book", return_value=book):
            result = client.get_order_book_depth("TICK")
        assert result["yes_ask_size"] == 200
        assert result["no_ask_size"] == 150
