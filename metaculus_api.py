"""Metaculus API client for community probability forecasts.

Metaculus provides calibrated community predictions as a read-only signal
source. The public REST API at https://www.metaculus.com/api2/ works without
authentication (with tighter rate limits). An optional API key unlocks higher
rate limits.

This is a READ-ONLY client -- no trading, no order placement.
"""

import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

METACULUS_API_URL = "https://www.metaculus.com/api2"

# Rate limiting (thread-safe)
_last_request_time = 0
_rate_lock = threading.Lock()
MIN_REQUEST_INTERVAL = 1.0  # 1000ms between requests


def _rate_limit():
    """Enforce minimum interval between requests (thread-safe)."""
    global _last_request_time
    with _rate_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.time()


class MetaculusClient:
    """Metaculus community prediction API client (read-only)."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
        })
        self.authenticated = False
        self.base_url = METACULUS_API_URL

    def login(self, api_key: str | None = None) -> bool:
        """Authenticate with Metaculus API token.

        Public API works without a key (stricter rate limits). If an API key
        is provided, it is sent as an ``Authorization: Token`` header for
        higher rate limits.

        Falls back to the ``METACULUS_API_KEY`` environment variable.

        Args:
            api_key: Optional Metaculus API key.

        Returns:
            True if the client is ready to make requests.
        """
        api_key = api_key or os.getenv("METACULUS_API_KEY")

        if api_key:
            self.session.headers.update({
                "Authorization": f"Token {api_key}",
            })

        # Verify connectivity by fetching a single question
        _rate_limit()
        try:
            resp = self.session.get(
                f"{self.base_url}/questions/",
                params={"limit": 1},
                timeout=30,
            )
            if resp.status_code == 200:
                self.authenticated = True
                logger.info("Metaculus client ready (api_key=%s)",
                            "yes" if api_key else "no")
                return True
            logger.error("Metaculus verification failed: %s", resp.status_code)
            return False
        except requests.RequestException as exc:
            logger.error("Metaculus verification request failed: %s", exc)
            return False

    def _request(self, method: str, endpoint: str,
                 params: dict = None) -> dict | None:
        """Make an API request with rate limiting.

        Args:
            method: HTTP method (GET, POST, etc.).
            endpoint: API path relative to base URL.
            params: Query parameters.

        Returns:
            Response JSON or None on failure.
        """
        _rate_limit()
        try:
            url = f"{self.base_url}{endpoint}"
            resp = self.session.request(method, url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("Metaculus %s %s returned %s: %s",
                           method, endpoint, resp.status_code,
                           resp.text[:200])
            return None
        except requests.RequestException as exc:
            logger.warning("Metaculus %s %s failed: %s", method, endpoint, exc)
            return None

    def fetch_active_questions(self, limit: int = 200, offset: int = 0,
                               category: str = None) -> list[dict]:
        """Fetch open forecast questions.

        Args:
            limit: Maximum number of questions to return per page.
            offset: Pagination offset.
            category: Optional search/category filter string.

        Returns:
            List of question dicts. Empty list on failure.
        """
        params = {
            "status": "open",
            "limit": limit,
            "offset": offset,
            "type": "forecast",
        }
        if category:
            params["search"] = category

        data = self._request("GET", "/questions/", params=params)
        if not data:
            return []

        results = data.get("results", [])
        return results

    def get_question_prediction(self, question_id: int) -> float | None:
        """Get the community median prediction for a question.

        Extracts ``community_prediction.full.q2`` (the median) from the
        question detail endpoint.

        Args:
            question_id: Metaculus question ID.

        Returns:
            Probability (0-1) or None if unavailable.
        """
        data = self._request("GET", f"/questions/{question_id}/")
        if not data:
            return None

        try:
            prediction = data["community_prediction"]["full"]["q2"]
            return float(prediction)
        except (KeyError, TypeError, ValueError):
            logger.debug("No community prediction for question %s",
                         question_id)
            return None

    def search_questions(self, query: str, limit: int = 50) -> list[dict]:
        """Search for open forecast questions by text query.

        Args:
            query: Search string to match against question titles.
            limit: Maximum results to return.

        Returns:
            List of matching question dicts. Empty list on failure.
        """
        params = {
            "search": query,
            "status": "open",
            "limit": limit,
            "type": "forecast",
        }

        data = self._request("GET", "/questions/", params=params)
        if not data:
            return []

        return data.get("results", [])

    def get_question_details(self, question_id: int) -> dict | None:
        """Fetch full details for a specific question.

        Args:
            question_id: Metaculus question ID.

        Returns:
            Full question dict or None on failure.
        """
        return self._request("GET", f"/questions/{question_id}/")
