"""ForecastEx (Interactive Brokers) API client for prediction market data and trading."""

import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

# IBKR Client Portal Gateway API
IBKR_GATEWAY_URL = "https://localhost:5000/v1/api"
FORECASTEX_API_URL = "https://www.forecastex.com/api/v1"

# Rate limiting
_last_request_time = 0
_rate_lock = threading.Lock()
MIN_REQUEST_INTERVAL = 0.2  # 200ms between requests


def _rate_limit():
    global _last_request_time
    with _rate_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.time()


class ForecastExClient:
    """ForecastEx/IBKR prediction market API client.

    Uses the IBKR Client Portal Gateway for authentication and trading,
    and the ForecastEx public API for market data.

    Special constraint: ForecastEx contracts cannot be sold.
    To exit a position, you must buy the opposing side.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.verify = False  # IBKR gateway uses self-signed certs
        self.authenticated = False
        self.account_id = None

    def login(self, username: str = None, password: str = None) -> bool:
        """Authenticate with IBKR Client Portal Gateway.

        The Client Portal Gateway must be running locally.
        Falls back to IBKR_USERNAME/IBKR_PASSWORD env vars.
        """
        username = username or os.getenv("IBKR_USERNAME")
        password = password or os.getenv("IBKR_PASSWORD")

        if not username or not password:
            # Try public API mode (no trading, just market data)
            logger.info("ForecastEx: no IBKR credentials, using public API mode")
            self.authenticated = True  # Can still read public data
            return True

        _rate_limit()
        try:
            # Check if gateway session is already active
            resp = self.session.get(
                f"{IBKR_GATEWAY_URL}/iserver/auth/status",
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("authenticated"):
                    self.authenticated = True
                    # Get account ID
                    acct_resp = self.session.get(
                        f"{IBKR_GATEWAY_URL}/portfolio/accounts",
                        timeout=10,
                    )
                    if acct_resp.status_code == 200:
                        accounts = acct_resp.json()
                        if accounts:
                            self.account_id = accounts[0].get("accountId")
                    logger.info("ForecastEx: IBKR gateway session active (account: %s)", self.account_id)
                    return True

            logger.warning("ForecastEx: IBKR gateway not authenticated. Run 'ibkr-gateway' and login via browser.")
            # Fall back to public API mode
            self.authenticated = True
            return True

        except requests.RequestException as e:
            logger.warning("ForecastEx: IBKR gateway connection failed: %s. Using public API.", e)
            self.authenticated = True
            return True

    def _request(self, method: str, endpoint: str, params: dict = None, json_data: dict = None, base_url: str = None) -> dict | None:
        """Make an API request."""
        url_base = base_url or FORECASTEX_API_URL
        _rate_limit()
        try:
            url = f"{url_base}{endpoint}"
            resp = self.session.request(method, url, params=params, json=json_data, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("ForecastEx %s %s returned %s: %s", method, endpoint, resp.status_code, resp.text[:200])
            return None
        except requests.RequestException as e:
            logger.warning("ForecastEx %s %s failed: %s", method, endpoint, e)
            return None

    def fetch_all_markets(self) -> list[dict]:
        """Fetch active ForecastEx markets.

        ForecastEx markets are binary event contracts that trade between $0-$1.
        """
        data = self._request("GET", "/markets", params={"status": "open"})
        if data and isinstance(data, list):
            return data
        if data and "markets" in data:
            return data["markets"]
        if data and "data" in data:
            return data["data"]
        return []

    def get_market_price(self, market: dict) -> tuple[float | None, float | None]:
        """Extract YES/NO prices from a ForecastEx market.

        ForecastEx binary contracts have a single price that represents
        the YES probability. NO price = 1 - YES price.
        """
        # Try various field names
        yes_price = (
            market.get("lastPrice")
            or market.get("yesPrice")
            or market.get("yes_price")
            or market.get("bestYesBid")
        )
        no_price = (
            market.get("noPrice")
            or market.get("no_price")
            or market.get("bestNoBid")
        )

        if yes_price is not None:
            try:
                yes_price = float(yes_price)
            except (ValueError, TypeError):
                yes_price = None

        if no_price is not None:
            try:
                no_price = float(no_price)
            except (ValueError, TypeError):
                no_price = None

        if yes_price is not None and no_price is None:
            no_price = 1.0 - yes_price
        elif no_price is not None and yes_price is None:
            yes_price = 1.0 - no_price

        return yes_price, no_price

    def place_order(self, contract_id: str, side: str, price: float, quantity: int = 1) -> dict | None:
        """Place an order on ForecastEx via IBKR gateway.

        Note: ForecastEx contracts cannot be sold. Both arbitrage legs
        are BUY operations (buy YES + buy NO).

        Args:
            contract_id: IBKR contract ID for the ForecastEx contract.
            side: 'BUY' (selling is not supported).
            price: Limit price ($0-$1).
            quantity: Number of contracts.
        """
        if not self.authenticated or not self.account_id:
            logger.error("ForecastEx: must be authenticated with IBKR to trade")
            return None

        if side.upper() != "BUY":
            logger.error("ForecastEx: only BUY orders supported (contracts cannot be sold)")
            return None

        _rate_limit()
        try:
            resp = self.session.post(
                f"{IBKR_GATEWAY_URL}/iserver/account/{self.account_id}/orders",
                json={
                    "orders": [{
                        "conid": int(contract_id),
                        "orderType": "LMT",
                        "side": "BUY",
                        "price": price,
                        "quantity": quantity,
                        "tif": "DAY",
                    }],
                },
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                # IBKR may return confirmation prompts
                if isinstance(data, list) and data:
                    order_data = data[0]
                    if order_data.get("id"):
                        return order_data
                    # May need order confirmation
                    reply_id = order_data.get("id") or order_data.get("replyId")
                    if reply_id:
                        # Confirm the order
                        confirm_resp = self.session.post(
                            f"{IBKR_GATEWAY_URL}/iserver/reply/{reply_id}",
                            json={"confirmed": True},
                            timeout=15,
                        )
                        if confirm_resp.status_code == 200:
                            return confirm_resp.json()
                return data if data else None
            logger.error("ForecastEx place_order: %s %s", resp.status_code, resp.text[:200])
            return None
        except requests.RequestException as e:
            logger.error("ForecastEx place_order failed: %s", e)
            return None

    def get_balance(self) -> float | None:
        """Get available IBKR account balance."""
        if not self.authenticated or not self.account_id:
            return None

        data = self._request("GET", f"/portfolio/{self.account_id}/summary",
                            base_url=IBKR_GATEWAY_URL)
        if data:
            # IBKR returns balance under various keys
            cash = data.get("availablefunds", data.get("totalcashvalue", {}))
            if isinstance(cash, dict):
                return float(cash.get("amount", 0))
            return float(cash) if cash else None
        return None

    def get_order_status(self, order_id: str) -> dict | None:
        """Get status of a specific IBKR order."""
        if not self.authenticated:
            return None
        return self._request("GET", f"/iserver/account/orders/{order_id}",
                           base_url=IBKR_GATEWAY_URL)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an IBKR order."""
        if not self.authenticated or not self.account_id:
            return False
        _rate_limit()
        try:
            resp = self.session.delete(
                f"{IBKR_GATEWAY_URL}/iserver/account/{self.account_id}/order/{order_id}",
                timeout=30,
            )
            return resp.status_code == 200
        except requests.RequestException as e:
            logger.warning("ForecastEx cancel_order failed: %s", e)
            return False
