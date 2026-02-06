"""WebSocket real-time price feeds for Polymarket and Kalshi."""

import asyncio
import json
import time
from typing import Callable

import websockets

from kalshi_api import KALSHI_BASE_URL, KALSHI_API_PATH, _sign_pss, _load_private_key

# WebSocket endpoints
KALSHI_WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

RECONNECT_DELAY = 5  # seconds before reconnect attempt
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
                print(f"  [WARN] Could not load Kalshi private key for WS: {e}")

        self._kalshi_tickers: list[str] = []
        self._poly_token_ids: list[str] = []
        self._running = False

    def subscribe_kalshi(self, tickers: list[str]):
        """Set Kalshi market tickers to subscribe to."""
        self._kalshi_tickers = list(set(tickers))

    def subscribe_polymarket(self, token_ids: list[str]):
        """Set Polymarket CLOB token IDs to subscribe to."""
        self._poly_token_ids = list(set(token_ids))

    async def run(self):
        """Run both WebSocket connections concurrently with auto-reconnect."""
        self._running = True
        tasks = []
        if self._kalshi_tickers and self.kalshi_api_key_id and self.kalshi_private_key:
            tasks.append(self._run_kalshi())
        if self._poly_token_ids:
            tasks.append(self._run_polymarket())

        if not tasks:
            print("  [WS] No subscriptions configured.")
            return

        print(f"  [WS] Starting feeds: {len(self._kalshi_tickers)} Kalshi tickers, "
              f"{len(self._poly_token_ids)} Polymarket tokens")
        await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self):
        """Signal feeds to stop."""
        self._running = False

    async def _run_kalshi(self):
        """Maintain Kalshi WebSocket connection with auto-reconnect."""
        while self._running:
            try:
                await self._connect_kalshi()
            except Exception as e:
                if self._running:
                    print(f"  [WS] Kalshi connection error: {e}. Reconnecting in {RECONNECT_DELAY}s...")
                    await asyncio.sleep(RECONNECT_DELAY)

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

        async with websockets.connect(KALSHI_WS_URL, additional_headers=headers) as ws:
            print(f"  [WS] Kalshi connected. Subscribing to {len(self._kalshi_tickers)} tickers...")

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

            # Read messages with keepalive
            while self._running:
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

    def _handle_kalshi_message(self, data: dict):
        """Process a Kalshi WebSocket message."""
        msg_type = data.get("type", "")
        if msg_type == "orderbook_snapshot" or msg_type == "orderbook_delta":
            ticker = data.get("msg", {}).get("market_ticker", "")
            if ticker:
                self.on_price_update("kalshi", ticker, data.get("msg", {}))

    async def _run_polymarket(self):
        """Maintain Polymarket WebSocket connection with auto-reconnect."""
        while self._running:
            try:
                await self._connect_polymarket()
            except Exception as e:
                if self._running:
                    print(f"  [WS] Polymarket connection error: {e}. Reconnecting in {RECONNECT_DELAY}s...")
                    await asyncio.sleep(RECONNECT_DELAY)

    async def _connect_polymarket(self):
        """Connect to Polymarket CLOB WebSocket and subscribe to markets."""
        async with websockets.connect(POLYMARKET_WS_URL) as ws:
            print(f"  [WS] Polymarket connected. Subscribing to {len(self._poly_token_ids)} tokens...")

            # Subscribe to market updates — batch into groups
            # Polymarket WS accepts asset subscriptions
            for token_id in self._poly_token_ids:
                sub_msg = {
                    "type": "market",
                    "assets_ids": [token_id],
                }
                await ws.send(json.dumps(sub_msg))

            while self._running:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=KEEPALIVE_INTERVAL)
                    data = json.loads(raw)
                    self._handle_polymarket_message(data)
                except asyncio.TimeoutError:
                    try:
                        await ws.ping()
                    except Exception:
                        break

    def _handle_polymarket_message(self, data: dict | list):
        """Process a Polymarket WebSocket message."""
        # Polymarket sends arrays of events
        events = data if isinstance(data, list) else [data]
        for event in events:
            asset_id = event.get("asset_id", "")
            if asset_id:
                self.on_price_update("polymarket", asset_id, event)
