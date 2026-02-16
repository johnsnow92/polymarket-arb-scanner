"""PredictIt API client for market data and trading."""

import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

PREDICTIT_BASE = "https://www.predictit.org"
PREDICTIT_API = f"{PREDICTIT_BASE}/api/marketdata"

# Rate limiting (thread-safe)
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


# PredictIt regulatory limits (CFTC)
MAX_POSITION_PER_CONTRACT = 850  # $850 per-contract position limit


class PredictItClient:
    """PredictIt API client for market data and session-based trading."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self.authenticated = False

    def login(self, email: str = None, password: str = None) -> bool:
        """Authenticate via session-based login.

        Args:
            email: PredictIt email (falls back to PREDICTIT_EMAIL env var).
            password: PredictIt password (falls back to PREDICTIT_PASSWORD env var).

        Returns:
            True if login succeeded.
        """
        email = email or os.getenv("PREDICTIT_EMAIL")
        password = password or os.getenv("PREDICTIT_PASSWORD")
        if not email or not password:
            logger.error("PredictIt credentials not provided")
            return False

        _rate_limit()
        try:
            resp = self.session.post(
                f"{PREDICTIT_BASE}/api/Account/token",
                json={"email": email, "password": password},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("access_token")
                if token:
                    self.session.headers["Authorization"] = f"Bearer {token}"
                    self.authenticated = True
                    return True
            logger.error("PredictIt login failed: %s", resp.status_code)
            return False
        except requests.RequestException as e:
            logger.error("PredictIt login request failed: %s", e)
            return False

    def fetch_all_markets(self) -> list[dict]:
        """Fetch all markets from PredictIt public API.

        Returns:
            List of market dicts with nested contracts.
        """
        _rate_limit()
        try:
            resp = self.session.get(
                f"{PREDICTIT_API}/all/",
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("markets", [])
        except requests.RequestException as e:
            logger.warning("PredictIt fetch_all_markets failed: %s", e)
            return []

    def fetch_market(self, market_id: int) -> dict | None:
        """Fetch a single market by ID.

        Args:
            market_id: PredictIt market ID.

        Returns:
            Market dict or None on failure.
        """
        _rate_limit()
        try:
            resp = self.session.get(
                f"{PREDICTIT_API}/markets/{market_id}",
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning("PredictIt fetch_market %s: %s", market_id, resp.status_code)
            return None
        except requests.RequestException as e:
            logger.warning("PredictIt fetch_market %s failed: %s", market_id, e)
            return None

    def place_order(
        self,
        contract_id: int,
        side: str,
        price: float,
        quantity: int,
    ) -> dict | None:
        """Place a buy or sell order on a PredictIt contract.

        Args:
            contract_id: PredictIt contract ID.
            side: 'yes' or 'no'.
            price: Price per share (0.01-0.99).
            quantity: Number of shares.

        Returns:
            Order response dict or None on failure.
        """
        if not self.authenticated:
            logger.error("PredictIt: must login before placing orders")
            return None

        # Enforce CFTC position limit
        if quantity * price > MAX_POSITION_PER_CONTRACT:
            logger.warning("PredictIt: order exceeds $850 position limit")
            return None

        _rate_limit()
        try:
            trade_type = "Buy" if side.lower() in ("yes", "buy") else "Sell"
            resp = self.session.post(
                f"{PREDICTIT_BASE}/api/Trade/submitTrade",
                json={
                    "contractId": contract_id,
                    "tradeType": trade_type,
                    "pricePerShare": price,
                    "quantity": quantity,
                },
                timeout=30,
            )
            if resp.status_code in (200, 201):
                return resp.json()
            logger.error("PredictIt place_order failed: %s %s", resp.status_code, resp.text[:200])
            return None
        except requests.RequestException as e:
            logger.error("PredictIt place_order failed: %s", e)
            return None

    def get_positions(self) -> list[dict]:
        """Get current open positions.

        Returns:
            List of position dicts.
        """
        if not self.authenticated:
            logger.warning("PredictIt: must login to get positions")
            return []

        _rate_limit()
        try:
            resp = self.session.get(
                f"{PREDICTIT_BASE}/api/Profile/Shares",
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()
            return []
        except requests.RequestException as e:
            logger.warning("PredictIt get_positions failed: %s", e)
            return []

    def get_balance(self) -> float | None:
        """Get available account balance in dollars.

        Returns:
            Balance as float or None on failure.
        """
        if not self.authenticated:
            logger.warning("PredictIt: must login to get balance")
            return None

        _rate_limit()
        try:
            resp = self.session.get(
                f"{PREDICTIT_BASE}/api/Profile/balance",
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                return float(data.get("balance", 0))
            return None
        except requests.RequestException as e:
            logger.warning("PredictIt get_balance failed: %s", e)
            return None

    def get_order_status(self, order_id: int) -> dict | None:
        """Get status of a specific order.

        Args:
            order_id: PredictIt order ID.

        Returns:
            Order status dict or None.
        """
        if not self.authenticated:
            return None

        _rate_limit()
        try:
            resp = self.session.get(
                f"{PREDICTIT_BASE}/api/Trade/{order_id}",
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()
            return None
        except requests.RequestException as e:
            logger.warning("PredictIt get_order_status failed: %s", e)
            return None

    def get_market_price(self, market: dict) -> tuple[float | None, float | None]:
        """Extract best YES/NO prices from a PredictIt market.

        PredictIt markets have contracts with bestBuyYesCost and bestBuyNoCost.
        For binary markets (single contract), returns (yes_price, no_price).
        For multi-contract markets, returns prices for the first contract.

        Args:
            market: PredictIt market dict (from fetch_market or fetch_all_markets).

        Returns:
            (yes_price, no_price) tuple in dollar terms (0-1), or (None, None).
        """
        contracts = market.get("contracts", [])
        if not contracts:
            return None, None

        contract = contracts[0]
        yes_price = contract.get("bestBuyYesCost")
        no_price = contract.get("bestBuyNoCost")

        if yes_price is not None and no_price is not None:
            try:
                return float(yes_price), float(no_price)
            except (ValueError, TypeError) as e:
                logger.warning("PredictIt price parse error: %s", e)

        # Fallback to lastTradePrice
        last_price = contract.get("lastTradePrice")
        if last_price is not None:
            try:
                lp = float(last_price)
                return lp, 1.0 - lp
            except (ValueError, TypeError) as e:
                logger.warning("PredictIt lastTradePrice parse error: %s", e)

        return None, None
