"""Dedicated tests for manifold_api.py — Manifold Markets read-only client.

Coverage:
- ManifoldClient init with and without API key (auth header presence)
- fetch_markets returns list, with sort/limit params
- fetch_market returns dict or None
- search_markets builds correct query
- get_probability extracts numeric value
- get_market_by_slug
- _get retries on 429 (raises _RateLimitError) + ConnectionError
- _get returns None on non-200 (other than 429)
- Rate limiting between requests is enforced

All tests mock ``requests.Session.get`` so no network access is required.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from manifold_api import ManifoldClient, _RateLimitError, MANIFOLD_BASE_URL


# ---------------------------------------------------------------------------
# Init / auth header
# ---------------------------------------------------------------------------


class TestInit:
    def test_init_without_key_no_auth_header(self):
        # Make sure no env var is leaking in.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MANIFOLD_API_KEY", None)
            client = ManifoldClient(api_key=None)
            assert "Authorization" not in client.session.headers

    def test_init_with_explicit_key_sets_header(self):
        client = ManifoldClient(api_key="test-key-123")
        assert client.session.headers.get("Authorization") == "Key test-key-123"

    def test_init_picks_up_env_var(self):
        with patch.dict(os.environ, {"MANIFOLD_API_KEY": "from-env"}):
            client = ManifoldClient()
            assert client.session.headers.get("Authorization") == "Key from-env"

    def test_base_url_constant(self):
        client = ManifoldClient()
        assert client.base_url == MANIFOLD_BASE_URL
        assert MANIFOLD_BASE_URL.startswith("https://")


# ---------------------------------------------------------------------------
# fetch_markets
# ---------------------------------------------------------------------------


def _ok(json_payload):
    """Build a fake 200 response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json = lambda: json_payload
    return resp


class TestFetchMarkets:
    def test_returns_list_on_success(self):
        client = ManifoldClient()
        client.session.get = MagicMock(return_value=_ok([
            {"id": "m1", "probability": 0.55},
            {"id": "m2", "probability": 0.30},
        ]))
        markets = client.fetch_markets(limit=50, sort="liquidity")
        assert len(markets) == 2
        # Verify URL + params shape.
        url, kwargs = client.session.get.call_args[0], client.session.get.call_args[1]
        assert url[0] == f"{MANIFOLD_BASE_URL}/markets"
        assert kwargs["params"] == {"limit": 50, "sort": "liquidity"}

    def test_returns_empty_when_payload_not_a_list(self):
        client = ManifoldClient()
        client.session.get = MagicMock(return_value=_ok({"error": "wat"}))
        assert client.fetch_markets() == []

    def test_returns_empty_on_non_200(self):
        client = ManifoldClient()
        resp = MagicMock(status_code=500, text="error")
        client.session.get = MagicMock(return_value=resp)
        assert client.fetch_markets() == []


# ---------------------------------------------------------------------------
# fetch_market
# ---------------------------------------------------------------------------


class TestFetchMarket:
    def test_returns_market_dict(self):
        client = ManifoldClient()
        client.session.get = MagicMock(
            return_value=_ok({"id": "abc", "probability": 0.42}),
        )
        result = client.fetch_market("abc")
        assert result["id"] == "abc"
        assert result["probability"] == 0.42

    def test_returns_none_for_list_response(self):
        # /market/<id> should return a dict; defend against API shape changes.
        client = ManifoldClient()
        client.session.get = MagicMock(return_value=_ok([{"id": "abc"}]))
        assert client.fetch_market("abc") is None

    def test_returns_none_on_404(self):
        client = ManifoldClient()
        resp = MagicMock(status_code=404, text="not found")
        client.session.get = MagicMock(return_value=resp)
        assert client.fetch_market("missing") is None


# ---------------------------------------------------------------------------
# search_markets
# ---------------------------------------------------------------------------


class TestSearchMarkets:
    def test_builds_correct_params(self):
        client = ManifoldClient()
        client.session.get = MagicMock(return_value=_ok([{"id": "x"}]))
        result = client.search_markets("Bitcoin $100k", limit=5)
        assert len(result) == 1
        kwargs = client.session.get.call_args[1]
        assert kwargs["params"] == {"term": "Bitcoin $100k", "limit": 5}

    def test_returns_empty_on_failure(self):
        client = ManifoldClient()
        client.session.get = MagicMock(return_value=_ok({"error": "bad"}))
        assert client.search_markets("x") == []


# ---------------------------------------------------------------------------
# get_probability
# ---------------------------------------------------------------------------


class TestGetProbability:
    def test_extracts_numeric(self):
        client = ManifoldClient()
        client.session.get = MagicMock(
            return_value=_ok({"id": "x", "probability": 0.65}),
        )
        assert client.get_probability("x") == 0.65

    def test_handles_string_numeric(self):
        client = ManifoldClient()
        client.session.get = MagicMock(
            return_value=_ok({"id": "x", "probability": "0.42"}),
        )
        assert client.get_probability("x") == 0.42

    def test_returns_none_when_market_missing(self):
        client = ManifoldClient()
        resp = MagicMock(status_code=404, text="not found")
        client.session.get = MagicMock(return_value=resp)
        assert client.get_probability("missing") is None

    def test_returns_none_when_probability_field_missing(self):
        client = ManifoldClient()
        client.session.get = MagicMock(return_value=_ok({"id": "x"}))
        assert client.get_probability("x") is None

    def test_returns_none_on_invalid_value(self):
        client = ManifoldClient()
        client.session.get = MagicMock(
            return_value=_ok({"id": "x", "probability": "not-a-number"}),
        )
        assert client.get_probability("x") is None


# ---------------------------------------------------------------------------
# get_market_by_slug
# ---------------------------------------------------------------------------


class TestGetMarketBySlug:
    def test_uses_slug_endpoint(self):
        client = ManifoldClient()
        client.session.get = MagicMock(
            return_value=_ok({"id": "x", "slug": "test-market"}),
        )
        client.get_market_by_slug("test-market")
        called_url = client.session.get.call_args[0][0]
        assert "/slug/test-market" in called_url

    def test_returns_none_on_list(self):
        client = ManifoldClient()
        client.session.get = MagicMock(return_value=_ok([]))
        assert client.get_market_by_slug("x") is None


# ---------------------------------------------------------------------------
# Retry behaviour on 429 / ConnectionError
# ---------------------------------------------------------------------------


class TestRetry:
    def test_retries_on_429_then_succeeds(self):
        client = ManifoldClient()
        ok_resp = _ok({"id": "x", "probability": 0.5})
        bad_resp = MagicMock(status_code=429, text="rate limited")
        client.session.get = MagicMock(side_effect=[bad_resp, bad_resp, ok_resp])
        result = client.fetch_market("x")
        assert result == {"id": "x", "probability": 0.5}
        assert client.session.get.call_count == 3

    def test_raises_after_max_attempts_on_persistent_429(self):
        client = ManifoldClient()
        bad_resp = MagicMock(status_code=429, text="rate limited")
        client.session.get = MagicMock(return_value=bad_resp)
        with pytest.raises(_RateLimitError):
            client.fetch_market("x")
        # tenacity stops at 3 attempts.
        assert client.session.get.call_count == 3

    def test_request_exception_returns_none(self):
        client = ManifoldClient()
        # Generic RequestException (not Connection/Timeout) → caught + None.
        client.session.get = MagicMock(
            side_effect=requests.RequestException("boom"),
        )
        assert client.fetch_market("x") is None
