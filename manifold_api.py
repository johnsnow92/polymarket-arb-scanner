"""Manifold Markets API client for market data and betting."""

import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

MANIFOLD_BASE = "https://api.manifold.markets/v0"

# Rate limiting (thread-safe)
_last_request_time = 0
_rate_lock = threading.Lock()
MIN_REQUEST_INTERVAL = 0.1  # 100ms between requests


def _rate_limit():
    global _last_request_time
    with _rate_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.time()


class ManifoldClient:
    """Manifold Markets API client with optional authentication."""

    def __init__(self, api_key: str = None):
        """Initialize the client.

        Args:
            api_key: Manifold API key (falls back to MANIFOLD_API_KEY env var).
                     Required for placing bets and account info.
                     Market data is publicly accessible without auth.
        """
        self.session = requests.Session()
        self.api_key = api_key or os.getenv("MANIFOLD_API_KEY")
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if self.api_key:
            self.session.headers["Authorization"] = f"Key {self.api_key}"

    def fetch_markets(self, limit: int = 1000, sweepstakes_only: bool = True) -> list[dict]:
        """Fetch markets from Manifold.

        Args:
            limit: Maximum number of markets to return (default 1000).
            sweepstakes_only: If True, only return sweepstakes (real-money) markets
                              where token == "CASH". Play-money (mana) markets are
                              excluded since they can't produce real arbitrage profit.

        Returns:
            List of market dicts.
        """
        _rate_limit()
        try:
            resp = self.session.get(
                f"{MANIFOLD_BASE}/markets",
                params={"limit": limit},
                timeout=30,
            )
            resp.raise_for_status()
            markets = resp.json()
            if sweepstakes_only:
                markets = [m for m in markets if m.get("token") == "CASH"]
            return markets
        except requests.RequestException as e:
            logger.warning("Manifold fetch_markets failed: %s", e)
            return []

    def search_markets(self, term: str) -> list[dict]:
        """Search markets by text query.

        Args:
            term: Search query string.

        Returns:
            List of matching market dicts.
        """
        _rate_limit()
        try:
            resp = self.session.get(
                f"{MANIFOLD_BASE}/search-markets",
                params={"term": term},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning("Manifold search_markets failed: %s", e)
            return []

    def fetch_market(self, market_id: str) -> dict | None:
        """Fetch a single market by ID or slug.

        Args:
            market_id: Manifold market ID or slug.

        Returns:
            Market dict or None on failure.
        """
        _rate_limit()
        try:
            resp = self.session.get(
                f"{MANIFOLD_BASE}/market/{market_id}",
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return None
            logger.warning("Manifold fetch_market %s: %s", market_id, resp.status_code)
            return None
        except requests.RequestException as e:
            logger.warning("Manifold fetch_market %s failed: %s", market_id, e)
            return None

    def place_bet(
        self,
        market_id: str,
        outcome: str,
        amount: float,
    ) -> dict | None:
        """Place a bet on a Manifold market.

        Args:
            market_id: Manifold market ID (contract ID).
            outcome: 'YES' or 'NO'.
            amount: Amount to bet in mana.

        Returns:
            Bet response dict or None on failure.
        """
        if not self.api_key:
            logger.error("Manifold: API key required for placing bets")
            return None

        _rate_limit()
        try:
            resp = self.session.post(
                f"{MANIFOLD_BASE}/bet",
                json={
                    "contractId": market_id,
                    "outcome": outcome.upper(),
                    "amount": amount,
                },
                timeout=30,
            )
            if resp.status_code in (200, 201):
                return resp.json()
            logger.error("Manifold place_bet failed: %s %s", resp.status_code, resp.text[:200])
            return None
        except requests.RequestException as e:
            logger.error("Manifold place_bet failed: %s", e)
            return None

    def get_positions(self) -> list[dict]:
        """Get current user's positions/bets.

        Returns:
            List of bet dicts.
        """
        if not self.api_key:
            logger.warning("Manifold: API key required to get positions")
            return []

        _rate_limit()
        try:
            # First get user info to get user ID
            resp = self.session.get(
                f"{MANIFOLD_BASE}/me",
                timeout=30,
            )
            if resp.status_code != 200:
                return []
            user = resp.json()
            user_id = user.get("id")
            if not user_id:
                return []

            # Fetch bets for this user
            _rate_limit()
            resp = self.session.get(
                f"{MANIFOLD_BASE}/bets",
                params={"userId": user_id, "limit": 100},
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()
            return []
        except (requests.RequestException, ValueError) as e:
            logger.warning("Manifold get_positions failed: %s", e)
            return []

    def get_balance(self) -> float | None:
        """Get current user's mana balance.

        Returns:
            Balance as float or None on failure.
        """
        if not self.api_key:
            logger.warning("Manifold: API key required to get balance")
            return None

        _rate_limit()
        try:
            resp = self.session.get(
                f"{MANIFOLD_BASE}/me",
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                return float(data.get("balance", 0))
            return None
        except requests.RequestException as e:
            logger.warning("Manifold get_balance failed: %s", e)
            return None

    def get_order_status(self, bet_id: str) -> dict | None:
        """Get status of a specific bet.

        Args:
            bet_id: Manifold bet ID.

        Returns:
            Bet dict or None.
        """
        _rate_limit()
        try:
            resp = self.session.get(
                f"{MANIFOLD_BASE}/bets",
                params={"id": bet_id},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and data:
                    return data[0]
                elif isinstance(data, dict):
                    return data
            return None
        except requests.RequestException as e:
            logger.warning("Manifold get_order_status failed: %s", e)
            return None

    def get_market_price(self, market: dict) -> tuple[float | None, float | None]:
        """Extract YES/NO prices from a Manifold market.

        Manifold binary markets have a probability field that represents the
        current market probability. YES price = probability, NO price = 1 - probability.

        Args:
            market: Manifold market dict (from fetch_markets, search_markets, or fetch_market).

        Returns:
            (yes_price, no_price) in probability terms (0-1), or (None, None).
        """
        # Only binary markets have a single probability
        outcome_type = market.get("outcomeType", "")
        if outcome_type not in ("BINARY", "PSEUDO_NUMERIC"):
            return None, None

        prob = market.get("probability")
        if prob is None:
            return None, None

        try:
            yes_price = float(prob)
            no_price = 1.0 - yes_price
            return yes_price, no_price
        except (ValueError, TypeError):
            return None, None
