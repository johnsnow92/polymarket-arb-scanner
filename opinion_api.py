"""Opinion Exchange API client (BNB Chain prediction market)."""

import logging
import os
import threading
import time
import requests

logger = logging.getLogger(__name__)

OPINION_API_URL = "https://api.opinion.xyz/v1"

_last_request_time = 0
_rate_lock = threading.Lock()
MIN_REQUEST_INTERVAL = 0.05

def _rate_limit():
    global _last_request_time
    with _rate_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.time()


class OpinionClient:
    """Opinion Exchange API client for BNB Chain prediction markets."""

    def __init__(self):
        self.session = requests.Session()
        self.authenticated = False
        self.wallet_address = None

    def login(self, private_key: str = None) -> bool:
        """Authenticate with Opinion via wallet signature.

        For data-only mode, authentication is optional — public API works without auth.
        """
        private_key = private_key or os.getenv("OPINION_PRIVATE_KEY")
        if private_key:
            # Wallet auth would go here (web3 signature)
            self.authenticated = True
            logger.info("Opinion: wallet key configured for trading")
        else:
            # Public API mode — no auth needed for reading
            self.authenticated = True
            logger.info("Opinion: running in public API mode (read-only)")

        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        return True

    def _request(self, method: str, endpoint: str, params: dict = None, json_data: dict = None) -> dict | None:
        _rate_limit()
        try:
            url = f"{OPINION_API_URL}{endpoint}"
            resp = self.session.request(method, url, params=params, json=json_data, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("Opinion %s %s returned %s: %s", method, endpoint, resp.status_code, resp.text[:200])
            return None
        except requests.RequestException as e:
            logger.warning("Opinion %s %s failed: %s", method, endpoint, e)
            return None

    def fetch_all_markets(self) -> list[dict]:
        """Fetch active markets from Opinion."""
        data = self._request("GET", "/markets", params={"status": "active", "limit": 200})
        if data and isinstance(data, list):
            return data
        if data and "markets" in data:
            return data["markets"]
        if data and "data" in data:
            return data["data"]
        return []

    def get_market_price(self, market: dict) -> tuple[float | None, float | None]:
        """Extract YES/NO prices from an Opinion market."""
        yes_price = market.get("yesPrice", market.get("yes_price"))
        no_price = market.get("noPrice", market.get("no_price"))

        if yes_price is not None:
            yes_price = float(yes_price)
        if no_price is not None:
            no_price = float(no_price)

        if yes_price is not None and no_price is None:
            no_price = 1.0 - yes_price
        elif no_price is not None and yes_price is None:
            yes_price = 1.0 - no_price

        return yes_price, no_price

    def get_orderbook(self, market_id: str) -> dict | None:
        """Fetch orderbook for a market."""
        return self._request("GET", f"/markets/{market_id}/orderbook")

    def place_order(self, market_id: str, side: str, price: float, size: float) -> dict | None:
        """Place an order on Opinion."""
        if not self.authenticated:
            return None
        _rate_limit()
        try:
            resp = self.session.post(
                f"{OPINION_API_URL}/orders",
                json={"marketId": market_id, "side": side, "price": price, "size": size},
                timeout=30,
            )
            if resp.status_code in (200, 201):
                return resp.json()
            logger.error("Opinion place_order: %s %s", resp.status_code, resp.text[:200])
            return None
        except requests.RequestException as e:
            logger.error("Opinion place_order failed: %s", e)
            return None

    def get_balance(self) -> float | None:
        """Fetch account balance."""
        if not self.authenticated:
            return None
        data = self._request("GET", "/accounts/balance")
        if data:
            return float(data.get("balance", data.get("available", 0)))
        return None

    def get_order_status(self, order_id: str) -> dict | None:
        """Fetch status of a placed order."""
        return self._request("GET", f"/orders/{order_id}")

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        if not self.authenticated:
            return False
        _rate_limit()
        try:
            resp = self.session.delete(f"{OPINION_API_URL}/orders/{order_id}", timeout=30)
            return resp.status_code in (200, 204)
        except requests.RequestException:
            return False
