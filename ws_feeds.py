"""WebSocket real-time price feeds for Polymarket and Kalshi."""

import asyncio
import json
import logging
import os
import time
from typing import Callable

logger = logging.getLogger(__name__)

import websockets

from kalshi_api import KALSHI_BASE_URL, KALSHI_API_PATH, _sign_pss, _load_private_key

# Proxy support for WebSocket connections
try:
    from python_socks.async_.asyncio import Proxy
    _SOCKS_AVAILABLE = True
except ImportError:
    _SOCKS_AVAILABLE = False

# WebSocket endpoints
KALSHI_WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

RECONNECT_DELAY = 5  # initial seconds before reconnect attempt
RECONNECT_MAX_DELAY = 60  # maximum backoff delay
KEEPALIVE_INTERVAL = 10  # seconds between pings


class FeedManager:
    """Manages WebSocket connections to both platforms for real-time price feeds."""

    def __init__(
        self,
        on_price_update: Callable[[str, str, dict], None],
        kalshi_api_key_id: str | None = None,
        kalshi_private_key_path: str | None = None,
    ):
        """
        Args:
            on_price_update: Callback(platform, ticker/token_id, price_data)
            kalshi_api_key_id: Kalshi API key ID for WS auth
            kalshi_private_key_path: Path to Kalshi RSA private key
        """
        self.on_price_update = on_price_update
        self.kalshi_api_key_id = kalshi_api_key_id
        self.kalshi_private_key = None
        if kalshi_private_key_path:
            try:
                self.kalshi_private_key = _load_private_key(kalshi_private_key_path)
            except Exception as e:
                logger.warning("Could not load Kalshi private key for WS: %s", e)

        self._kalshi_tickers: list[str] = []
        self._poly_token_ids: list[str] = []
        self._running = False
        self._pm_proxy = os.getenv("POLYMARKET_PROXY_URL")
        self._kalshi_proxy = os.getenv("KALSHI_PROXY_URL")
        self._pending_poly_subs: list[str] = []
        self._pending_kalshi_subs: list[str] = []
        self._kalshi_ws = None
        self._poly_ws = None

    def subscribe_kalshi(self, tickers: list[str]):
        """Set Kalshi market tickers to subscribe to."""
        self._kalshi_tickers = list(set(tickers))

    def subscribe_polymarket(self, token_ids: list[str]):
        """Set Polymarket CLOB token IDs to subscribe to."""
        self._poly_token_ids = list(set(token_ids))

    def update_subscriptions(self, poly_token_ids: list[str] | None = None,
                             kalshi_tickers: list[str] | None = None):
        """Dynamically add new subscriptions without reconnecting.

        New tokens/tickers are queued and sent on the next message loop iteration.
        """
        if poly_token_ids:
            new_poly = [t for t in poly_token_ids if t and t not in self._poly_token_ids]
            if new_poly:
                self._poly_token_ids.extend(new_poly)
                self._pending_poly_subs.extend(new_poly)
                logger.info("Queued %d new Polymarket subscriptions.", len(new_poly))

        if kalshi_tickers:
            new_kalshi = [t for t in kalshi_tickers if t and t not in self._kalshi_tickers]
            if new_kalshi:
                self._kalshi_tickers.extend(new_kalshi)
                self._pending_kalshi_subs.extend(new_kalshi)
                logger.info("Queued %d new Kalshi subscriptions.", len(new_kalshi))

    async def run(self):
        """Run both WebSocket connections concurrently with auto-reconnect."""
        self._running = True
        tasks = []
        if self._kalshi_tickers and self.kalshi_api_key_id and self.kalshi_private_key:
            tasks.append(self._run_kalshi())
        if self._poly_token_ids:
            tasks.append(self._run_polymarket())

        if not tasks:
            logger.info("No subscriptions configured.")
            return

        logger.info("Starting feeds: %d Kalshi tickers, %d Polymarket tokens",
                    len(self._kalshi_tickers), len(self._poly_token_ids))
        await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self):
        """Signal feeds to stop."""
        self._running = False

    async def _run_kalshi(self):
        """Maintain Kalshi WebSocket connection with auto-reconnect and exponential backoff."""
        delay = RECONNECT_DELAY
        while self._running:
            try:
                await self._connect_kalshi()
                delay = RECONNECT_DELAY  # Reset backoff on successful connection
            except Exception as e:
                if self._running:
                    logger.warning("Kalshi connection error: %s. Reconnecting in %ds...", e, delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _connect_kalshi(self):
        """Connect to Kalshi WebSocket and subscribe to tickers."""
        # Build auth headers for WS connection
        import datetime
        timestamp_ms = str(int(datetime.datetime.now().timestamp() * 1000))
        msg = timestamp_ms + "GET" + KALSHI_API_PATH + "/ws/v2"
        signature = _sign_pss(self.kalshi_private_key, msg)

        headers = {
            "KALSHI-ACCESS-KEY": self.kalshi_api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "Content-Type": "application/json",
        }

        # Use proxy if configured
        connect_kwargs = {"additional_headers": headers}
        if self._kalshi_proxy and _SOCKS_AVAILABLE:
            proxy = Proxy.from_url(self._kalshi_proxy)
            sock = await proxy.connect(dest_host="api.elections.kalshi.com", dest_port=443)
            connect_kwargs["sock"] = sock

        async with websockets.connect(KALSHI_WS_URL, **connect_kwargs) as ws:
            logger.info("Kalshi connected. Subscribing to %d tickers...", len(self._kalshi_tickers))

            # Subscribe to orderbook updates for each ticker
            for ticker in self._kalshi_tickers:
                sub_msg = {
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["orderbook_delta"],
                        "market_tickers": [ticker],
                    },
                }
                await ws.send(json.dumps(sub_msg))

            self._kalshi_ws = ws

            # Read messages with keepalive
            while self._running:
                # Send any pending subscriptions
                while self._pending_kalshi_subs:
                    ticker = self._pending_kalshi_subs.pop(0)
                    sub_msg = {
                        "id": 1,
                        "cmd": "subscribe",
                        "params": {
                            "channels": ["orderbook_delta"],
                            "market_tickers": [ticker],
                        },
                    }
                    await ws.send(json.dumps(sub_msg))

                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=KEEPALIVE_INTERVAL)
                    data = json.loads(raw)
                    self._handle_kalshi_message(data)
                except asyncio.TimeoutError:
                    # Send ping to keep connection alive
                    try:
                        await ws.ping()
                    except Exception:
                        break

            self._kalshi_ws = None

    def _handle_kalshi_message(self, data: dict):
        """Process a Kalshi WebSocket message."""
        msg_type = data.get("type", "")
        if msg_type == "orderbook_snapshot" or msg_type == "orderbook_delta":
            ticker = data.get("msg", {}).get("market_ticker", "")
            if ticker:
                self.on_price_update("kalshi", ticker, data.get("msg", {}))

    async def _run_polymarket(self):
        """Maintain Polymarket WebSocket connection with auto-reconnect and exponential backoff."""
        delay = RECONNECT_DELAY
        while self._running:
            try:
                await self._connect_polymarket()
                delay = RECONNECT_DELAY  # Reset backoff on successful connection
            except Exception as e:
                if self._running:
                    logger.warning("Polymarket connection error: %s. Reconnecting in %ds...", e, delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _connect_polymarket(self):
        """Connect to Polymarket CLOB WebSocket and subscribe to markets."""
        # Use proxy if configured
        connect_kwargs = {}
        if self._pm_proxy and _SOCKS_AVAILABLE:
            proxy = Proxy.from_url(self._pm_proxy)
            sock = await proxy.connect(dest_host="ws-subscriptions-clob.polymarket.com", dest_port=443)
            connect_kwargs["sock"] = sock

        async with websockets.connect(POLYMARKET_WS_URL, **connect_kwargs) as ws:
            logger.info("Polymarket connected. Subscribing to %d tokens...", len(self._poly_token_ids))

            # Subscribe to market updates — batch into groups
            # Polymarket WS accepts asset subscriptions
            for token_id in self._poly_token_ids:
                sub_msg = {
                    "type": "market",
                    "assets_ids": [token_id],
                }
                await ws.send(json.dumps(sub_msg))

            self._poly_ws = ws

            while self._running:
                # Send any pending subscriptions
                while self._pending_poly_subs:
                    token_id = self._pending_poly_subs.pop(0)
                    sub_msg = {
                        "type": "market",
                        "assets_ids": [token_id],
                    }
                    await ws.send(json.dumps(sub_msg))

                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=KEEPALIVE_INTERVAL)
                    data = json.loads(raw)
                    self._handle_polymarket_message(data)
                except asyncio.TimeoutError:
                    try:
                        await ws.ping()
                    except Exception:
                        break

            self._poly_ws = None

    def _handle_polymarket_message(self, data: dict | list):
        """Process a Polymarket WebSocket message."""
        # Polymarket sends arrays of events
        events = data if isinstance(data, list) else [data]
        for event in events:
            asset_id = event.get("asset_id", "")
            if asset_id:
                self.on_price_update("polymarket", asset_id, event)
