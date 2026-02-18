"""Tests for Metaculus API client."""

import sys
import os
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from metaculus_api import MetaculusClient, _rate_limit, MIN_REQUEST_INTERVAL


class TestMetaculusLogin:
    def test_login_succeeds_with_api_key(self):
        """Login with an explicit API key sets auth header and returns True."""
        client = MetaculusClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": []}

        with patch.object(client.session, "get", return_value=mock_resp):
            with patch("metaculus_api._rate_limit"):
                result = client.login(api_key="test-key-123")

        assert result is True
        assert client.authenticated is True
        assert client.session.headers["Authorization"] == "Token test-key-123"

    def test_login_succeeds_without_api_key(self):
        """Login without a key still works (public API)."""
        client = MetaculusClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": []}

        with patch.object(client.session, "get", return_value=mock_resp):
            with patch("metaculus_api._rate_limit"):
                with patch.dict(os.environ, {}, clear=False):
                    # Ensure no env var is set
                    os.environ.pop("METACULUS_API_KEY", None)
                    result = client.login()

        assert result is True
        assert client.authenticated is True
        assert "Authorization" not in client.session.headers

    def test_login_falls_back_to_env_var(self):
        """Login reads METACULUS_API_KEY from env if no arg provided."""
        client = MetaculusClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": []}

        with patch.object(client.session, "get", return_value=mock_resp):
            with patch("metaculus_api._rate_limit"):
                with patch.dict(os.environ, {"METACULUS_API_KEY": "env-key-456"}):
                    result = client.login()

        assert result is True
        assert client.session.headers["Authorization"] == "Token env-key-456"

    def test_login_fails_on_http_error(self):
        """Login returns False when verification request fails."""
        client = MetaculusClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 403

        with patch.object(client.session, "get", return_value=mock_resp):
            with patch("metaculus_api._rate_limit"):
                result = client.login(api_key="bad-key")

        assert result is False
        assert client.authenticated is False

    def test_login_fails_on_request_exception(self):
        """Login returns False on network error."""
        client = MetaculusClient()

        import requests
        with patch.object(client.session, "get",
                          side_effect=requests.RequestException("timeout")):
            with patch("metaculus_api._rate_limit"):
                result = client.login(api_key="test-key")

        assert result is False
        assert client.authenticated is False


class TestMetaculusRequest:
    def test_request_returns_json_on_success(self):
        """_request returns parsed JSON on 200 response."""
        client = MetaculusClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": [{"id": 1}]}

        with patch.object(client.session, "request", return_value=mock_resp):
            with patch("metaculus_api._rate_limit"):
                result = client._request("GET", "/questions/")

        assert result == {"results": [{"id": 1}]}

    def test_request_returns_none_on_error_status(self):
        """_request returns None on non-200 status."""
        client = MetaculusClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch.object(client.session, "request", return_value=mock_resp):
            with patch("metaculus_api._rate_limit"):
                result = client._request("GET", "/questions/1/")

        assert result is None

    def test_request_returns_none_on_exception(self):
        """_request returns None on network exception."""
        client = MetaculusClient()

        import requests
        with patch.object(client.session, "request",
                          side_effect=requests.RequestException("connection")):
            with patch("metaculus_api._rate_limit"):
                result = client._request("GET", "/questions/")

        assert result is None

    def test_request_calls_rate_limit(self):
        """_request invokes _rate_limit before each call."""
        client = MetaculusClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}

        with patch.object(client.session, "request", return_value=mock_resp):
            with patch("metaculus_api._rate_limit") as mock_rl:
                client._request("GET", "/questions/")

        mock_rl.assert_called_once()


class TestFetchActiveQuestions:
    def test_returns_parsed_questions(self):
        """fetch_active_questions returns list of question dicts."""
        client = MetaculusClient()
        questions = [
            {"id": 100, "title": "Will X happen?"},
            {"id": 101, "title": "Will Y happen?"},
        ]

        with patch.object(client, "_request",
                          return_value={"results": questions}):
            result = client.fetch_active_questions(limit=50)

        assert len(result) == 2
        assert result[0]["id"] == 100
        assert result[1]["title"] == "Will Y happen?"

    def test_passes_correct_params(self):
        """fetch_active_questions sends status, limit, offset, type params."""
        client = MetaculusClient()

        with patch.object(client, "_request",
                          return_value={"results": []}) as mock_req:
            client.fetch_active_questions(limit=100, offset=50)

        mock_req.assert_called_once_with("GET", "/questions/", params={
            "status": "open",
            "limit": 100,
            "offset": 50,
            "type": "forecast",
        })

    def test_passes_category_as_search(self):
        """fetch_active_questions adds search param when category provided."""
        client = MetaculusClient()

        with patch.object(client, "_request",
                          return_value={"results": []}) as mock_req:
            client.fetch_active_questions(category="politics")

        mock_req.assert_called_once_with("GET", "/questions/", params={
            "status": "open",
            "limit": 200,
            "offset": 0,
            "type": "forecast",
            "search": "politics",
        })

    def test_returns_empty_list_on_failure(self):
        """fetch_active_questions returns [] when API returns None."""
        client = MetaculusClient()

        with patch.object(client, "_request", return_value=None):
            result = client.fetch_active_questions()

        assert result == []

    def test_returns_empty_list_when_no_results_key(self):
        """fetch_active_questions returns [] when response has no results."""
        client = MetaculusClient()

        with patch.object(client, "_request", return_value={}):
            result = client.fetch_active_questions()

        assert result == []


class TestGetQuestionPrediction:
    def test_extracts_median_probability(self):
        """get_question_prediction returns q2 (median) from community prediction."""
        client = MetaculusClient()

        question_data = {
            "id": 42,
            "title": "Will X happen?",
            "community_prediction": {
                "full": {
                    "q1": 0.25,
                    "q2": 0.65,
                    "q3": 0.85,
                }
            }
        }

        with patch.object(client, "_request", return_value=question_data):
            result = client.get_question_prediction(42)

        assert result == pytest.approx(0.65)

    def test_returns_none_when_no_prediction(self):
        """get_question_prediction returns None when community_prediction missing."""
        client = MetaculusClient()

        with patch.object(client, "_request",
                          return_value={"id": 42, "title": "Test"}):
            result = client.get_question_prediction(42)

        assert result is None

    def test_returns_none_when_prediction_incomplete(self):
        """get_question_prediction returns None when nested keys missing."""
        client = MetaculusClient()

        with patch.object(client, "_request",
                          return_value={"id": 42,
                                        "community_prediction": {"full": {}}}):
            result = client.get_question_prediction(42)

        assert result is None

    def test_returns_none_on_api_failure(self):
        """get_question_prediction returns None when API call fails."""
        client = MetaculusClient()

        with patch.object(client, "_request", return_value=None):
            result = client.get_question_prediction(999)

        assert result is None

    def test_returns_none_when_prediction_is_none(self):
        """get_question_prediction handles None values in prediction chain."""
        client = MetaculusClient()

        with patch.object(client, "_request",
                          return_value={"id": 42,
                                        "community_prediction": None}):
            result = client.get_question_prediction(42)

        assert result is None


class TestSearchQuestions:
    def test_passes_search_params(self):
        """search_questions sends search, status, limit, type params."""
        client = MetaculusClient()

        with patch.object(client, "_request",
                          return_value={"results": []}) as mock_req:
            client.search_questions("election 2026", limit=25)

        mock_req.assert_called_once_with("GET", "/questions/", params={
            "search": "election 2026",
            "status": "open",
            "limit": 25,
            "type": "forecast",
        })

    def test_returns_matching_questions(self):
        """search_questions returns list of matched question dicts."""
        client = MetaculusClient()
        questions = [{"id": 10, "title": "Election question"}]

        with patch.object(client, "_request",
                          return_value={"results": questions}):
            result = client.search_questions("election")

        assert len(result) == 1
        assert result[0]["id"] == 10

    def test_returns_empty_list_on_failure(self):
        """search_questions returns [] when API fails."""
        client = MetaculusClient()

        with patch.object(client, "_request", return_value=None):
            result = client.search_questions("nonexistent")

        assert result == []


class TestGetQuestionDetails:
    def test_returns_full_question_dict(self):
        """get_question_details returns the full question response."""
        client = MetaculusClient()
        question = {
            "id": 42,
            "title": "Will X happen?",
            "url": "https://www.metaculus.com/questions/42/",
            "resolution_criteria": "Resolves YES if...",
            "created_time": "2025-01-01T00:00:00Z",
            "close_time": "2026-12-31T23:59:59Z",
            "community_prediction": {"full": {"q1": 0.2, "q2": 0.5, "q3": 0.8}},
            "possibilities": {"type": "binary"},
        }

        with patch.object(client, "_request", return_value=question):
            result = client.get_question_details(42)

        assert result["id"] == 42
        assert result["title"] == "Will X happen?"
        assert result["possibilities"]["type"] == "binary"

    def test_returns_none_on_failure(self):
        """get_question_details returns None when API fails."""
        client = MetaculusClient()

        with patch.object(client, "_request", return_value=None):
            result = client.get_question_details(999)

        assert result is None


class TestRateLimiting:
    def test_min_request_interval_is_one_second(self):
        """Rate limit interval is 1.0 second for Metaculus."""
        assert MIN_REQUEST_INTERVAL == 1.0

    def test_rate_limit_function_exists(self):
        """_rate_limit is a callable function."""
        assert callable(_rate_limit)

    def test_rate_limit_sleeps_when_called_rapidly(self):
        """_rate_limit sleeps to enforce minimum interval."""
        import metaculus_api

        # Reset the global timestamp so next call thinks one was just made
        original = metaculus_api._last_request_time
        metaculus_api._last_request_time = 0

        try:
            with patch("metaculus_api.time.sleep") as mock_sleep:
                with patch("metaculus_api.time.time", side_effect=[100.0, 100.0]):
                    # _last_request_time is 0, time.time() returns 100.0
                    # elapsed = 100.0, which is >= 1.0, so no sleep needed
                    _rate_limit()
                    mock_sleep.assert_not_called()

            # Now simulate a rapid second call
            metaculus_api._last_request_time = 100.0
            with patch("metaculus_api.time.sleep") as mock_sleep:
                with patch("metaculus_api.time.time", side_effect=[100.3, 100.3]):
                    # elapsed = 0.3, need to sleep 0.7
                    _rate_limit()
                    mock_sleep.assert_called_once()
                    sleep_duration = mock_sleep.call_args[0][0]
                    assert sleep_duration == pytest.approx(0.7, abs=0.05)
        finally:
            metaculus_api._last_request_time = original
