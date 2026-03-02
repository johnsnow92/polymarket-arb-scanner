"""SX Bet Exchange API client for market data and trading."""

import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

SXBET_API_URL = "https://api.sx.bet"

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
    """SX Bet Exchange API client.

    SX Bet REST API is unauthenticated for read endpoints.  Trading
    (posting/filling orders) requires Ethereum wallet signatures in the
    request body — not header-based auth.  The ``X-Api-Key`` header is only
    needed for WebSocket token requests and heartbeat.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self.wallet_address: str | None = None
        self.private_key: str | None = None
        self.authenticated = False

    def login(self, api_key: str = None, private_key: str = None) -> bool:
        """Connect to SX Bet and verify API reachability.

        SX Bet read endpoints require no authentication.  The ``api_key``
        parameter (or ``SXBET_API_KEY`` env var) is treated as the wallet
        address for trading.  The optional ``private_key`` (or
        ``SXBET_PRIVATE_KEY`` env var) is stored for order signing.

        Args:
            api_key: Wallet address (0x…).  Falls back to env var.
            private_key: Private key for signing.  Falls back to env var.

        Returns:
            True if the SX Bet API is reachable.
        """
        self.wallet_address = api_key or os.getenv("SXBET_API_KEY")
        self.private_key = private_key or os.getenv("SXBET_PRIVATE_KEY")

        if not self.wallet_address:
            logger.error("SX Bet wallet address not provided")
            return False

        # Verify by fetching sports list (no auth header needed)
        _rate_limit()
        try:
            resp = self.session.get(f"{SXBET_API_URL}/sports", timeout=15)
            if resp.status_code == 200:
                self.authenticated = True
                logger.info("SX Bet connected (wallet=%s…%s)",
                            self.wallet_address[:6], self.wallet_address[-4:])
                return True
            logger.error("SX Bet API reachability check failed: %s", resp.status_code)
            return False
        except requests.RequestException as exc:
            logger.error("SX Bet API request failed: %s", exc)
            return False

    def _request(self, method: str, endpoint: str, params: dict = None,
                 json_data: dict = None) -> dict | None:
        """Make an API request.

        SX Bet read endpoints are unauthenticated.  No API key header is
        sent for normal requests.

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

        # Get sports list
        sports = self._request("GET", "/sports")
        if not sports or "data" not in sports:
            return all_markets

        for sport in sports.get("data", []):
            sport_id = sport.get("sportId")
            if not sport_id:
                continue

            # Fetch markets for this sport (SX Bet max pageSize = 50)
            market_data = self._request("GET", "/markets/active", params={
                "sportId": sport_id,
                "pageSize": 50,
            })
            if market_data and "data" in market_data:
                for market in market_data["data"]:
                    if not isinstance(market, dict):
                        logger.debug("SX Bet: skipping non-dict market entry: %s", type(market).__name__)
                        continue
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

        The ``/user/balance`` endpoint requires a real UUID API key
        (``X-Api-Key`` header), which is separate from the wallet address.
        We make a best-effort attempt; callers should tolerate ``None``.

        Returns:
            Balance as float or None on failure.
        """
        if not self.authenticated:
            return None

        # The balance endpoint requires X-Api-Key (UUID), not wallet address.
        # Try the documented endpoint; fall back gracefully.
        _rate_limit()
        try:
            resp = self.session.get(f"{SXBET_API_URL}/user/balance", timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                return float(data.get("balance", data.get("availableBalance", 0)))
            logger.debug("SX Bet balance unavailable (requires API key UUID): %s",
                         resp.status_code)
            return None
        except requests.RequestException as exc:
            logger.debug("SX Bet balance request failed: %s", exc)
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

    def get_market_status(self, market_hash: str) -> dict | None:
        """Get the current status of a market.

        Args:
            market_hash: SX Bet market hash.

        Returns:
            Market dict with status info, or None on failure.
        """
        return self._request("GET", f"/markets/{market_hash}")

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
