"""SX Bet Exchange API client for market data and trading."""

import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

SXBET_API_URL = "https://api.sx.bet/v2"

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


class SXBetClient:
    """SX Bet Exchange API client."""

    def __init__(self):
        self.session = requests.Session()
        self.api_key = None
        self.authenticated = False

    def login(self, api_key: str = None) -> bool:
        """Authenticate with SX Bet API key.

        Falls back to SXBET_API_KEY env var.

        Args:
            api_key: SX Bet API key.

        Returns:
            True if authentication succeeded.
        """
        api_key = api_key or os.getenv("SXBET_API_KEY")
        if not api_key:
            logger.error("SX Bet API key not provided")
            return False

        self.api_key = api_key
        self.session.headers.update({
            "X-Api-Key": api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

        # Verify by fetching active sports
        _rate_limit()
        try:
            resp = self.session.get(f"{SXBET_API_URL}/sports/active", timeout=15)
            if resp.status_code == 200:
                self.authenticated = True
                return True
            logger.error("SX Bet auth verification failed: %s", resp.status_code)
            return False
        except requests.RequestException as exc:
            logger.error("SX Bet auth request failed: %s", exc)
            return False

    def _request(self, method: str, endpoint: str, params: dict = None,
                 json_data: dict = None) -> dict | None:
        """Make an API request.

        Args:
            method: HTTP method (GET, POST, etc.).
            endpoint: API path relative to base URL.
            params: Query parameters.
            json_data: JSON body payload.

        Returns:
            Response JSON or None on failure.
        """
        if not self.authenticated:
            logger.error("SX Bet: must login before making API calls")
            return None

        _rate_limit()
        try:
            url = f"{SXBET_API_URL}{endpoint}"
            resp = self.session.request(method, url, params=params,
                                        json=json_data, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("SX Bet %s %s returned %s: %s",
                           method, endpoint, resp.status_code, resp.text[:200])
            return None
        except requests.RequestException as exc:
            logger.warning("SX Bet %s %s failed: %s", method, endpoint, exc)
            return None

    def fetch_all_markets(self) -> list[dict]:
        """Fetch active markets from SX Bet.

        Iterates over active sports and fetches markets for each.

        Returns:
            List of market dicts, each with an attached ``_sport`` parent.
        """
        all_markets = []

        # Get active sports first
        sports = self._request("GET", "/sports/active")
        if not sports or "data" not in sports:
            return all_markets

        for sport in sports.get("data", []):
            sport_id = sport.get("sportId")
            if not sport_id:
                continue

            # Fetch markets for this sport
            market_data = self._request("GET", "/markets/active", params={
                "sportId": sport_id,
                "pageSize": 100,
            })
            if market_data and "data" in market_data:
                for market in market_data["data"]:
                    market["_sport"] = sport
                    all_markets.append(market)

        return all_markets

    def get_market_price(self, market: dict) -> tuple[float | None, float | None]:
        """Extract best back/lay prices as YES/NO probabilities.

        SX Bet prices are already in the 0-1 probability range.

        Args:
            market: Market dict with a ``marketHash`` key.

        Returns:
            (yes_price, no_price) in probability terms (0-1), or (None, None).
        """
        market_hash = market.get("marketHash", "")
        if not market_hash:
            return None, None

        # Fetch orderbook for market
        data = self._request("GET", f"/markets/{market_hash}/orderbook")
        if not data:
            return None, None

        bids = data.get("bids", [])
        asks = data.get("asks", [])

        yes_price = None
        no_price = None

        # Best bid = highest buy price (YES equivalent)
        if bids:
            best_bid = bids[0]
            price = float(best_bid.get("price", 0))
            if price > 0:
                yes_price = price  # SX Bet prices already in 0-1 range

        # Best ask = lowest sell price
        if asks:
            best_ask = asks[0]
            price = float(best_ask.get("price", 0))
            if price > 0:
                no_price = 1.0 - price

        if yes_price is not None and no_price is None:
            no_price = 1.0 - yes_price
        elif no_price is not None and yes_price is None:
            yes_price = 1.0 - no_price

        return yes_price, no_price

    def list_runners(self, market_hash: str) -> list[dict]:
        """Fetch outcomes for a market.

        Args:
            market_hash: SX Bet market hash.

        Returns:
            List of outcome dicts.
        """
        data = self._request("GET", f"/markets/{market_hash}/outcomes")
        if data and "data" in data:
            return data["data"]
        return []

    def get_orderbook(self, market_hash: str) -> dict | None:
        """Fetch full orderbook for a market.

        Args:
            market_hash: SX Bet market hash.

        Returns:
            Orderbook dict or None.
        """
        return self._request("GET", f"/markets/{market_hash}/orderbook")

    def place_order(self, market_hash: str, outcome_id: str, side: str,
                    price: float, size: float) -> dict | None:
        """Place an order on SX Bet.

        Args:
            market_hash: SX Bet market hash.
            outcome_id: Outcome/runner ID.
            side: 'buy' or 'sell'.
            price: Price in probability (0-1).
            size: Stake amount.

        Returns:
            Order response dict or None on failure.
        """
        if not self.authenticated:
            return None

        _rate_limit()
        try:
            resp = self.session.post(
                f"{SXBET_API_URL}/orders",
                json={
                    "marketHash": market_hash,
                    "outcomeId": outcome_id,
                    "side": side,
                    "price": str(price),
                    "size": str(size),
                    "orderType": "limit",
                },
                timeout=30,
            )
            if resp.status_code in (200, 201):
                return resp.json()
            logger.error("SX Bet place_order: %s %s",
                         resp.status_code, resp.text[:200])
            return None
        except requests.RequestException as exc:
            logger.error("SX Bet place_order failed: %s", exc)
            return None

    def get_balance(self) -> float | None:
        """Get available account balance.

        Returns:
            Balance as float or None on failure.
        """
        if not self.authenticated:
            return None
        data = self._request("GET", "/accounts/balance")
        if data:
            return float(data.get("balance", data.get("availableBalance", 0)))
        return None

    def get_order_status(self, order_id: str) -> dict | None:
        """Get status of a specific order.

        Args:
            order_id: SX Bet order ID.

        Returns:
            Order dict or None.
        """
        if not self.authenticated:
            return None
        return self._request("GET", f"/orders/{order_id}")

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order.

        Args:
            order_id: SX Bet order ID.

        Returns:
            True if cancellation succeeded.
        """
        if not self.authenticated:
            return False
        _rate_limit()
        try:
            resp = self.session.delete(
                f"{SXBET_API_URL}/orders/{order_id}",
                timeout=30,
            )
            return resp.status_code in (200, 204)
        except requests.RequestException as exc:
            logger.warning("SX Bet cancel_order failed: %s", exc)
            return False
