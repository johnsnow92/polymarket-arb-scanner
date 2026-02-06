"""Kalshi API client with RSA-PSS API key authentication."""

import base64
import datetime
import threading
import time

import requests
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding

KALSHI_BASE_URL = "https://api.elections.kalshi.com"
KALSHI_API_PATH = "/trade-api/v2"

# Rate limiting (thread-safe)
_last_request_time = 0
_rate_lock = threading.Lock()
MIN_REQUEST_INTERVAL = 0.15  # 150ms between requests (conservative)


def _rate_limit():
    global _last_request_time
    with _rate_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.time()


def _load_private_key(file_path: str):
    """Load an RSA private key from a PEM file."""
    with open(file_path, "rb") as f:
        return serialization.load_pem_private_key(
            f.read(),
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


class KalshiClient:
    """Kalshi API client with RSA-PSS API key authentication."""

    def __init__(self):
        self.session = requests.Session()
        self.api_key_id = None
        self.private_key = None

    def login_with_api_key(self, api_key_id: str, private_key_path: str) -> bool:
        """Authenticate using API key ID + RSA private key file."""
        self.api_key_id = api_key_id
        try:
            self.private_key = _load_private_key(private_key_path)
            # Verify auth works with a lightweight call
            resp = self._request("GET", "/exchange/status")
            if resp and resp.status_code == 200:
                return True
            print(f"  [ERROR] Kalshi auth check returned status {resp.status_code if resp else 'None'}")
            return False
        except FileNotFoundError:
            print(f"  [ERROR] Private key file not found: {private_key_path}")
            return False
        except Exception as e:
            print(f"  [ERROR] Failed to load private key: {e}")
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

    def _request(self, method: str, path: str, params: dict = None, json_body: dict = None) -> requests.Response | None:
        """Make an authenticated request to Kalshi API."""
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
            return resp
        except requests.RequestException as e:
            print(f"  [WARN] Kalshi request failed ({method} {path}): {e}")
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
                print(f"  [WARN] Kalshi events request failed: {resp.status_code if resp else 'no response'}")
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
        if not resp or resp.status_code != 200:
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

        resp = self._request("POST", "/portfolio/orders", json_body=body)
        if not resp:
            return None
        if resp.status_code in (200, 201):
            return resp.json()
        print(f"  [ERROR] Kalshi place_order failed: {resp.status_code} {resp.text[:200]}")
        return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        resp = self._request("DELETE", f"/portfolio/orders/{order_id}")
        if resp and resp.status_code in (200, 204):
            return True
        print(f"  [WARN] Kalshi cancel_order failed for {order_id}: "
              f"{resp.status_code if resp else 'no response'}")
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
