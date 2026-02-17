"""Kalshi API client with RSA-PSS API key authentication."""

import base64
import datetime
import logging
import os
import threading
import time

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import KALSHI_RATE_LIMIT

KALSHI_BASE_URL = "https://api.elections.kalshi.com"
KALSHI_API_PATH = "/trade-api/v2"

# Rate limiting (thread-safe)
_last_request_time = 0
_rate_lock = threading.Lock()


def _rate_limit():
    global _last_request_time
    with _rate_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < KALSHI_RATE_LIMIT:
            time.sleep(KALSHI_RATE_LIMIT - elapsed)
        _last_request_time = time.time()


def _load_private_key(file_path: str):
    """Load an RSA private key from a PEM file."""
    with open(file_path, "rb") as f:
        return serialization.load_pem_private_key(
            f.read(),
            password=None,
            backend=default_backend(),
        )


def _load_private_key_from_base64(b64_string: str):
    """Load an RSA private key from a base64-encoded PEM string."""
    pem_bytes = base64.b64decode(b64_string)
    return serialization.load_pem_private_key(
        pem_bytes,
        password=None,
        backend=default_backend(),
    )


def _sign_pss(private_key, message: str) -> str:
    """Sign a message with RSA-PSS (SHA-256, salt=DIGEST_LENGTH) and return base64."""
    signature = private_key.sign(
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


class _RateLimitError(Exception):
    """Raised on HTTP 429 to trigger retry."""
    pass


class KalshiClient:
    """Kalshi API client with RSA-PSS API key authentication."""

    def __init__(self):
        self.session = requests.Session()
        # Proxy support
        proxy_url = os.getenv("KALSHI_PROXY_URL")
        if proxy_url:
            self.session.proxies = {"http": proxy_url, "https": proxy_url}
        self.session.mount("https://", HTTPAdapter(pool_connections=1, pool_maxsize=10))
        self.api_key_id = None
        self.private_key = None

    def login_with_api_key(self, api_key_id: str, private_key_path: str = None, private_key_base64: str = None) -> bool:
        """Authenticate using API key ID + RSA private key (file path or base64).

        Provide either private_key_path (PEM file) or private_key_base64
        (base64-encoded PEM string, e.g. for container deployments).
        """
        self.api_key_id = api_key_id
        try:
            if private_key_base64:
                self.private_key = _load_private_key_from_base64(private_key_base64)
            elif private_key_path:
                self.private_key = _load_private_key(private_key_path)
            else:
                logger.error("No Kalshi private key provided (need path or base64)")
                return False
            # Verify auth works with a lightweight call
            resp = self._request("GET", "/exchange/status")
            if resp and resp.status_code == 200:
                return True
            logger.error("Kalshi auth check returned status %s", resp.status_code if resp else 'None')
            return False
        except FileNotFoundError:
            logger.error("Private key file not found: %s", private_key_path)
            return False
        except Exception as e:
            logger.error("Failed to load private key: %s", e)
            return False

    def _auth_headers(self, method: str, path: str) -> dict:
        """Generate authentication headers for a request."""
        timestamp_ms = str(int(datetime.datetime.now().timestamp() * 1000))
        # Sign: timestamp + METHOD + path (no query params)
        path_no_query = path.split("?")[0]
        msg = timestamp_ms + method.upper() + KALSHI_API_PATH + path_no_query
        signature = _sign_pss(self.private_key, msg)

        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((_RateLimitError, requests.ConnectionError, requests.Timeout)),
        reraise=True,
    )
    def _request(self, method: str, path: str, params: dict = None, json_body: dict = None) -> requests.Response | None:
        """Make an authenticated request to Kalshi API with retry."""
        _rate_limit()
        headers = self._auth_headers(method, path)
        url = KALSHI_BASE_URL + KALSHI_API_PATH + path
        try:
            resp = self.session.request(
                method.upper(),
                url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=30,
            )
            if resp.status_code == 429:
                raise _RateLimitError(f"Rate limited: {method} {path}")
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            logger.warning("Kalshi request failed (%s %s): %s", method, path, e)
            raise
        except requests.RequestException as e:
            logger.warning("Kalshi request failed (%s %s): %s", method, path, e)
            return None

    def fetch_all_events(self, limit: int = 200, max_pages: int = 50) -> list[dict]:
        """Fetch all active events from Kalshi with cursor pagination."""
        all_events = []
        cursor = None

        for _ in range(max_pages):
            params = {"limit": limit, "status": "open"}
            if cursor:
                params["cursor"] = cursor

            resp = self._request("GET", "/events", params=params)
            if not resp or resp.status_code != 200:
                logger.warning("Kalshi events request failed: %s", resp.status_code if resp else 'no response')
                break

            data = resp.json()
            events = data.get("events", [])
            all_events.extend(events)

            cursor = data.get("cursor")
            if not cursor or not events:
                break

        return all_events

    def fetch_markets_for_event(self, event_ticker: str) -> list[dict]:
        """Fetch all markets for a specific event."""
        resp = self._request("GET", "/markets", params={
            "event_ticker": event_ticker,
            "limit": 100,
            "status": "open",
        })
        if not resp or resp.status_code != 200:
            return []
        return resp.json().get("markets", [])

    def fetch_order_book(self, ticker: str) -> dict | None:
        """Fetch order book for a given market ticker."""
        resp = self._request("GET", f"/markets/{ticker}/orderbook")
        if resp and resp.status_code == 200:
            return resp.json()
        return None

    def get_market_price(self, market: dict) -> tuple[float | None, float | None]:
        """Extract best yes/no prices from a Kalshi market.

        Returns (yes_price, no_price) in dollar terms (0-1).
        Uses dollar fields when available, falls back to cent fields.
        """
        # Prefer dollar-denominated fields
        yes_dollars = market.get("yes_ask_dollars")
        no_dollars = market.get("no_ask_dollars")

        if yes_dollars is not None and no_dollars is not None:
            try:
                yes_price = float(yes_dollars)
                no_price = float(no_dollars)
                if yes_price > 0 and no_price > 0:
                    return yes_price, no_price
            except (ValueError, TypeError):
                pass

        # Fallback to cent fields
        yes_ask = market.get("yes_ask")
        no_ask = market.get("no_ask")

        yes_price = yes_ask / 100.0 if yes_ask is not None else None
        no_price = no_ask / 100.0 if no_ask is not None else None

        if yes_price is None or no_price is None:
            return None, None

        return yes_price, no_price

    def get_balance(self) -> float | None:
        """Get account balance in dollars."""
        resp = self._request("GET", "/portfolio/balance")
        if resp is None or resp.status_code != 200:
            logger.warning("Kalshi get_balance failed: %s",
                           resp.status_code if resp is not None else "no response")
            return None
        data = resp.json()
        # Balance is returned in cents
        balance_cents = data.get("balance", 0)
        return balance_cents / 100.0

    def get_positions(self) -> list[dict]:
        """Get open positions."""
        resp = self._request("GET", "/portfolio/positions", params={"limit": 200})
        if not resp or resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("market_positions", [])

    def place_order(
        self,
        ticker: str,
        side: str,
        action: str,
        count: int,
        price_dollars: float,
        time_in_force: str = "fill_or_kill",
    ) -> dict | None:
        """Place a limit order on Kalshi.

        Args:
            ticker: Market ticker (e.g. "KXBTC-26FEB07-T101999.99")
            side: "yes" or "no"
            action: "buy" or "sell"
            count: Number of contracts
            price_dollars: Price per contract in dollars (0.01-0.99)
            time_in_force: "fill_or_kill" (default for arb safety) or "gtc"

        Returns:
            Order response dict or None on failure.
        """
        body = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "type": "limit",
            "time_in_force": time_in_force,
        }
        # Kalshi expects price in the appropriate side field
        if side == "yes":
            body["yes_price"] = int(round(price_dollars * 100))
        else:
            body["no_price"] = int(round(price_dollars * 100))

        try:
            resp = self._request("POST", "/portfolio/orders", json_body=body)
        except Exception as e:
            logger.error("Kalshi place_order exception: %s (ticker=%s)", e, ticker)
            return None
        if resp is None:
            logger.warning("Kalshi place_order got no response (ticker=%s body=%s)", ticker, body)
            return None
        if resp.status_code in (200, 201):
            return resp.json()
        logger.error("Kalshi place_order HTTP %s: %s (ticker=%s)", resp.status_code, resp.text[:300], ticker)
        return None

    def get_order_status(self, order_id: str) -> dict | None:
        """Get the status of a specific order.

        Returns dict with order details including 'status' field, or None.
        """
        resp = self._request("GET", f"/portfolio/orders/{order_id}")
        if resp is not None and resp.status_code == 200:
            data = resp.json()
            return data.get("order", data)
        return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        resp = self._request("DELETE", f"/portfolio/orders/{order_id}")
        if resp is not None and resp.status_code in (200, 204):
            return True
        logger.warning("Kalshi cancel_order failed for %s: %s", order_id,
                       resp.status_code if resp is not None else 'no response')
        return False

    def get_order_book_depth(self, ticker: str) -> dict | None:
        """Fetch order book and extract best bid/ask depth for a market.

        Returns dict with yes_ask_size, no_ask_size (number of contracts
        available at best price), or None on failure.
        """
        book = self.fetch_order_book(ticker)
        if not book:
            return None

        result = {"yes_ask_size": 0, "no_ask_size": 0}
        orderbook = book.get("orderbook", book)
        # Kalshi order book has "yes" and "no" sides
        yes_entries = orderbook.get("yes", [])
        no_entries = orderbook.get("no", [])
        if yes_entries:
            entry = yes_entries[0]
            if isinstance(entry, list) and len(entry) >= 2:
                result["yes_ask_size"] = int(entry[1])
            elif isinstance(entry, dict):
                result["yes_ask_size"] = int(entry.get("quantity", entry.get("size", 0)))
        if no_entries:
            entry = no_entries[0]
            if isinstance(entry, list) and len(entry) >= 2:
                result["no_ask_size"] = int(entry[1])
            elif isinstance(entry, dict):
                result["no_ask_size"] = int(entry.get("quantity", entry.get("size", 0)))
        return result
