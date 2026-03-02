"""WebSocket real-time price feeds for Polymarket, Kalshi, and Betfair."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

# Conditional metrics import — never breaks if metrics.py is missing
try:
    from config import METRICS_ENABLED as _METRICS_ENABLED
    if _METRICS_ENABLED:
        from metrics import metrics as _ws_metrics
    else:
        _ws_metrics = None
except Exception:
    _ws_metrics = None

import websockets

from kalshi_api import _sign_pss, _load_private_key, _load_private_key_from_base64

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
        kalshi_private_key_base64: str | None = None,
        betfair_app_key: str | None = None,
        betfair_session_token: str | None = None,
    ):
        """
        Args:
            on_price_update: Callback(platform, ticker/token_id, price_data)
            kalshi_api_key_id: Kalshi API key ID for WS auth
            kalshi_private_key_path: Path to Kalshi RSA private key PEM file
            kalshi_private_key_base64: Base64-encoded RSA private key (alternative to path)
            betfair_app_key: Betfair API application key for stream auth
            betfair_session_token: Betfair SSO session token (ssoid)
        """
        self.on_price_update = on_price_update
        self.kalshi_api_key_id = kalshi_api_key_id
        self.kalshi_private_key = None
        if kalshi_private_key_base64:
            try:
                self.kalshi_private_key = _load_private_key_from_base64(kalshi_private_key_base64)
            except Exception as e:
                logger.warning("Could not load Kalshi private key from base64 for WS: %s", e)
        elif kalshi_private_key_path:
            try:
                self.kalshi_private_key = _load_private_key(kalshi_private_key_path)
            except Exception as e:
                logger.warning("Could not load Kalshi private key for WS: %s", e)

        self._kalshi_tickers: list[str] = []
        self._poly_token_ids: list[str] = []
        self._betfair_market_ids: list[str] = []
        self._running = False
        self._pm_proxy = os.getenv("POLYMARKET_PROXY_URL")
        self._kalshi_proxy = os.getenv("KALSHI_PROXY_URL")
        self._pending_poly_subs: list[str] = []
        self._pending_kalshi_subs: list[str] = []
        self._pending_betfair_subs: list[str] = []
        self._kalshi_ws = None
        self._poly_ws = None

        # Betfair Stream API credentials
        self._betfair_app_key = betfair_app_key
        self._betfair_session_token = betfair_session_token
        self._betfair_feed: BetfairFeed | None = None

    def subscribe_kalshi(self, tickers: list[str]):
        """Set Kalshi market tickers to subscribe to."""
        self._kalshi_tickers = list(set(tickers))

    def subscribe_polymarket(self, token_ids: list[str]):
        """Set Polymarket CLOB token IDs to subscribe to."""
        self._poly_token_ids = list(set(token_ids))

    def subscribe_betfair(self, market_ids: list[str]):
        """Set Betfair market IDs to subscribe to via the Stream API."""
        self._betfair_market_ids = list(set(market_ids))

    def update_subscriptions(self, poly_token_ids: list[str] | None = None,
                             kalshi_tickers: list[str] | None = None,
                             betfair_market_ids: list[str] | None = None):
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

        if betfair_market_ids:
            new_bf = [m for m in betfair_market_ids if m and m not in self._betfair_market_ids]
            if new_bf:
                self._betfair_market_ids.extend(new_bf)
                self._pending_betfair_subs.extend(new_bf)
                logger.info("Queued %d new Betfair subscriptions.", len(new_bf))

    async def run(self):
        """Run all feed connections concurrently with auto-reconnect."""
        self._running = True
        tasks = []
        if self._kalshi_tickers and self.kalshi_api_key_id and self.kalshi_private_key:
            tasks.append(self._run_kalshi())
        if self._poly_token_ids:
            tasks.append(self._run_polymarket())
        if (self._betfair_market_ids and self._betfair_app_key
                and self._betfair_session_token):
            tasks.append(self._run_betfair())

        if not tasks:
            logger.info("No subscriptions configured.")
            return

        logger.info(
            "Starting feeds: %d Kalshi tickers, %d Polymarket tokens, %d Betfair markets",
            len(self._kalshi_tickers), len(self._poly_token_ids),
            len(self._betfair_market_ids),
        )
        await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self):
        """Signal feeds to stop."""
        self._running = False
        if self._betfair_feed:
            self._betfair_feed.stop()

    async def _run_betfair(self):
        """Maintain Betfair Stream API connection with auto-reconnect and exponential backoff."""
        from config import BETFAIR_STREAM_HOST, BETFAIR_STREAM_PORT

        cache = BetfairMarketCache()
        feed = BetfairFeed(
            app_key=self._betfair_app_key,
            session_token=self._betfair_session_token,
            market_ids=list(self._betfair_market_ids),
            on_price_update=self.on_price_update,
            cache=cache,
            host=BETFAIR_STREAM_HOST,
            port=BETFAIR_STREAM_PORT,
        )
        self._betfair_feed = feed

        delay = RECONNECT_DELAY
        while self._running:
            try:
                await feed.connect()
                delay = RECONNECT_DELAY
            except Exception as e:
                if self._running:
                    logger.warning("Betfair stream error: %s. Reconnecting in %ds...", e, delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, RECONNECT_MAX_DELAY)

            # Pick up any dynamically queued betfair subs
            while self._pending_betfair_subs:
                mid = self._pending_betfair_subs.pop(0)
                feed.add_market_ids([mid])

        self._betfair_feed = None

    async def _run_kalshi(self):
        """Maintain Kalshi WebSocket connection with auto-reconnect and exponential backoff."""
        delay = RECONNECT_DELAY
        while self._running:
            try:
                if _ws_metrics:
                    _ws_metrics.set("ws_connected", {"platform": "kalshi"}, value=1)
                await self._connect_kalshi()
                delay = RECONNECT_DELAY  # Reset backoff on successful connection
            except Exception as e:
                if self._running:
                    if _ws_metrics:
                        _ws_metrics.inc("ws_reconnections", {"platform": "kalshi"})
                        _ws_metrics.set("ws_connected", {"platform": "kalshi"}, value=0)
                    logger.warning("Kalshi connection error: %s. Reconnecting in %ds...", e, delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _connect_kalshi(self):
        """Connect to Kalshi WebSocket and subscribe to tickers."""
        # Build auth headers for WS connection
        # Per Kalshi docs, the WS signature signs: timestamp + "GET" + "/trade-api/ws/v2"
        # Note: this is NOT the REST API path (/trade-api/v2), it's the WS path.
        import datetime
        timestamp_ms = str(int(datetime.datetime.now().timestamp() * 1000))
        msg = timestamp_ms + "GET" + "/trade-api/ws/v2"
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
                    except Exception as e:
                        logger.debug("Kalshi ping failed: %s", e)
                        break

            self._kalshi_ws = None

    def _handle_kalshi_message(self, data: dict):
        """Process a Kalshi WebSocket message."""
        if _ws_metrics:
            _ws_metrics.inc("ws_messages_received", {"platform": "kalshi"})
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
                if _ws_metrics:
                    _ws_metrics.set("ws_connected", {"platform": "polymarket"}, value=1)
                await self._connect_polymarket()
                delay = RECONNECT_DELAY  # Reset backoff on successful connection
            except Exception as e:
                if self._running:
                    if _ws_metrics:
                        _ws_metrics.inc("ws_reconnections", {"platform": "polymarket"})
                        _ws_metrics.set("ws_connected", {"platform": "polymarket"}, value=0)
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

            # Subscribe to market updates in batches to avoid flooding the server.
            # Polymarket WS accepts arrays of asset IDs per message.
            batch_size = 100
            for i in range(0, len(self._poly_token_ids), batch_size):
                batch = self._poly_token_ids[i:i + batch_size]
                sub_msg = {
                    "type": "market",
                    "assets_ids": batch,
                }
                await ws.send(json.dumps(sub_msg))
                # Small delay between batches to avoid server rejection
                if i + batch_size < len(self._poly_token_ids):
                    await asyncio.sleep(0.1)

            self._poly_ws = ws

            while self._running:
                # Send any pending subscriptions (batched)
                if self._pending_poly_subs:
                    pending = list(self._pending_poly_subs)
                    self._pending_poly_subs.clear()
                    for i in range(0, len(pending), batch_size):
                        batch = pending[i:i + batch_size]
                        sub_msg = {
                            "type": "market",
                            "assets_ids": batch,
                        }
                        await ws.send(json.dumps(sub_msg))

                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=KEEPALIVE_INTERVAL)
                    data = json.loads(raw)
                    self._handle_polymarket_message(data)
                except asyncio.TimeoutError:
                    try:
                        await ws.ping()
                    except Exception as e:
                        logger.debug("Polymarket ping failed: %s", e)
                        break

            self._poly_ws = None

    def _handle_polymarket_message(self, data: dict | list):
        """Process a Polymarket WebSocket message."""
        if _ws_metrics:
            _ws_metrics.inc("ws_messages_received", {"platform": "polymarket"})
        # Polymarket sends arrays of events
        events = data if isinstance(data, list) else [data]
        for event in events:
            asset_id = event.get("asset_id", "")
            if asset_id:
                self.on_price_update("polymarket", asset_id, event)


# ---------------------------------------------------------------------------
# Betfair Exchange Streaming API
# ---------------------------------------------------------------------------


class BetfairMarketCache:
    """Thread-safe cache maintaining current Betfair market state from stream deltas.

    The Betfair Stream API sends a full image (img=True) followed by incremental
    deltas.  This cache merges both into the current best-price state for each
    (market_id, selection_id) and provides probability-converted prices that are
    compatible with the scanner's ``on_price_update`` callback format.

    Price ladder structure from Betfair:
        batb = [[level, price, volume], ...]  (best available to back)
        batl = [[level, price, volume], ...]  (best available to lay)

    Conversion to prediction-market probabilities:
        yes_price = 1 / back_odds
        no_price  = 1 - (1 / lay_odds)
    """

    def __init__(self):
        self._lock = threading.Lock()
        # {market_id: {selection_id: {"batb": [[l,p,v],...], "batl": [[l,p,v],...], "ltp": float}}}
        self._markets: dict[str, dict[int, dict]] = {}
        self.clk: str | None = None
        self.initial_clk: str | None = None

    def apply_market_change(self, mc: dict):
        """Merge a single market change message into the cache.

        Args:
            mc: A market change dict from the ``mc`` array in an ``mcm`` message.
                Contains ``id`` (market ID), optional ``img`` flag, and ``rc``
                (runner changes) with price ladder updates.
        """
        market_id = mc.get("id", "")
        if not market_id:
            return

        is_image = mc.get("img", False)

        with self._lock:
            if is_image:
                # Full image replaces existing state
                self._markets[market_id] = {}

            runners = self._markets.setdefault(market_id, {})

            for rc in mc.get("rc", []):
                sel_id = rc.get("id")
                if sel_id is None:
                    continue

                if is_image:
                    # Full replacement for this runner
                    runners[sel_id] = {
                        "batb": rc.get("batb", []),
                        "batl": rc.get("batl", []),
                        "ltp": rc.get("ltp"),
                    }
                else:
                    # Delta merge — update only provided ladder levels
                    existing = runners.setdefault(sel_id, {"batb": [], "batl": [], "ltp": None})

                    if "ltp" in rc:
                        existing["ltp"] = rc["ltp"]

                    for side in ("batb", "batl"):
                        if side in rc:
                            self._merge_ladder(existing[side], rc[side])

    @staticmethod
    def _merge_ladder(existing: list, updates: list):
        """Merge ladder level updates into existing ladder.

        Each entry is [level, price, volume].  Level 0 is best.
        A volume of 0 means the level should be removed.
        """
        level_map = {entry[0]: entry for entry in existing}
        for update in updates:
            level = update[0]
            volume = update[2] if len(update) > 2 else 0
            if volume == 0:
                level_map.pop(level, None)
            else:
                level_map[level] = update
        # Rebuild sorted by level
        existing.clear()
        existing.extend(sorted(level_map.values(), key=lambda x: x[0]))

    def get_best_prices(self, market_id: str, selection_id: int) -> dict | None:
        """Get the best back/lay prices for a runner as prediction-market probabilities.

        Args:
            market_id: Betfair market ID (e.g. "1.234567890").
            selection_id: Betfair selection (runner) ID.

        Returns:
            Dict with ``back_price``, ``lay_price``, ``yes_price``, ``no_price``,
            ``back_volume``, ``lay_volume``, and ``ltp`` — or None if not cached.
        """
        with self._lock:
            runners = self._markets.get(market_id, {})
            runner = runners.get(selection_id)
            if runner is None:
                return None

            result: dict = {
                "back_price": None,
                "lay_price": None,
                "yes_price": None,
                "no_price": None,
                "back_volume": None,
                "lay_volume": None,
                "ltp": runner.get("ltp"),
            }

            batb = runner.get("batb", [])
            batl = runner.get("batl", [])

            if batb:
                best_back = batb[0]  # level 0 = best
                odds = best_back[1]
                vol = best_back[2] if len(best_back) > 2 else 0
                if odds > 0:
                    result["back_price"] = odds
                    result["back_volume"] = vol
                    result["yes_price"] = 1.0 / odds

            if batl:
                best_lay = batl[0]
                odds = best_lay[1]
                vol = best_lay[2] if len(best_lay) > 2 else 0
                if odds > 0:
                    result["lay_price"] = odds
                    result["lay_volume"] = vol
                    result["no_price"] = 1.0 - (1.0 / odds)

            # Derive missing side if possible
            if result["yes_price"] is not None and result["no_price"] is None:
                result["no_price"] = 1.0 - result["yes_price"]
            elif result["no_price"] is not None and result["yes_price"] is None:
                result["yes_price"] = 1.0 - result["no_price"]

            return result

    def get_runners(self, market_id: str) -> dict[int, dict]:
        """Return all cached runners for a market.

        Returns:
            {selection_id: price_dict} for each runner in the market.
        """
        with self._lock:
            runners = self._markets.get(market_id, {})
            result = {}
            for sel_id in runners:
                # Release lock briefly per runner via get_best_prices (re-acquires)
                pass
        # Call outside lock to avoid deadlock
        with self._lock:
            sel_ids = list(self._markets.get(market_id, {}).keys())
        for sel_id in sel_ids:
            prices = self.get_best_prices(market_id, sel_id)
            if prices:
                result[sel_id] = prices
        return result

    def update_tokens(self, clk: str | None, initial_clk: str | None):
        """Store stream continuation tokens for reconnection.

        Args:
            clk: Current clock token from latest message.
            initial_clk: Initial clock token from subscription image.
        """
        with self._lock:
            if clk is not None:
                self.clk = clk
            if initial_clk is not None:
                self.initial_clk = initial_clk


class BetfairFeed:
    """Betfair Exchange Streaming API client using TLS TCP socket.

    Unlike the Polymarket and Kalshi feeds which use WebSocket, Betfair's
    Stream API uses a raw TLS TCP socket on stream-api.betfair.com:443 with
    CRLF-delimited JSON messages.

    Lifecycle:
        1. TLS connect to host:port
        2. Receive ``{"op": "connection", ...}`` from server
        3. Send authentication with app_key + session token
        4. Receive ``{"op": "status", "statusCode": "SUCCESS"}``
        5. Send market subscription with market IDs
        6. Receive initial image then deltas via ``mcm`` messages
    """

    def __init__(
        self,
        app_key: str,
        session_token: str,
        market_ids: list[str],
        on_price_update: Callable[[str, str, dict], None],
        cache: BetfairMarketCache,
        host: str = "stream-api.betfair.com",
        port: int = 443,
        heartbeat_ms: int = 5000,
    ):
        """
        Args:
            app_key: Betfair API application key.
            session_token: Betfair SSO session token (ssoid).
            market_ids: Initial list of market IDs to subscribe to.
            on_price_update: Callback(platform, market_id, price_data).
            cache: Shared BetfairMarketCache instance.
            host: Stream API hostname.
            port: Stream API port.
            heartbeat_ms: Server heartbeat interval in milliseconds.
        """
        self._app_key = app_key
        self._session_token = session_token
        self._market_ids = list(market_ids)
        self._on_price_update = on_price_update
        self._cache = cache
        self._host = host
        self._port = port
        self._heartbeat_ms = heartbeat_ms
        self._running = False
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._sub_id = 1
        self._pending_market_ids: list[str] = []

    def stop(self):
        """Signal the feed to stop."""
        self._running = False

    def add_market_ids(self, market_ids: list[str]):
        """Queue additional market IDs for subscription on next loop iteration."""
        new = [m for m in market_ids if m and m not in self._market_ids]
        if new:
            self._market_ids.extend(new)
            self._pending_market_ids.extend(new)

    async def connect(self):
        """Connect to the Betfair Stream API, authenticate, subscribe, and read messages."""
        self._running = True
        ssl_ctx = ssl.create_default_context()

        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port, ssl=ssl_ctx,
        )
        logger.info("Betfair stream: TCP connected to %s:%d", self._host, self._port)

        try:
            # Step 1: Receive connection message
            conn_msg = await self._read_line()
            if conn_msg.get("op") != "connection":
                raise ConnectionError(
                    f"Betfair stream: expected connection op, got {conn_msg.get('op')}"
                )
            conn_id = conn_msg.get("connectionId", "unknown")
            logger.info("Betfair stream: connected (connectionId=%s)", conn_id)

            # Step 2: Authenticate
            await self._send({
                "op": "authentication",
                "appKey": self._app_key,
                "session": self._session_token,
            })

            auth_resp = await self._read_line()
            if (auth_resp.get("op") != "status"
                    or auth_resp.get("statusCode") != "SUCCESS"):
                error_msg = auth_resp.get("errorMessage", auth_resp.get("statusCode", "unknown"))
                raise ConnectionError(f"Betfair stream auth failed: {error_msg}")
            logger.info("Betfair stream: authenticated")

            # Step 3: Subscribe to markets
            if self._market_ids:
                await self._subscribe(self._market_ids)
                logger.info("Betfair stream: subscribed to %d markets", len(self._market_ids))

            # Step 4: Read messages
            # Heartbeat timeout = heartbeat_ms * 2 to allow for network jitter
            timeout = (self._heartbeat_ms / 1000.0) * 2
            while self._running:
                # Send any pending subscription updates
                if self._pending_market_ids:
                    pending = list(self._pending_market_ids)
                    self._pending_market_ids.clear()
                    await self._subscribe(self._market_ids)  # Re-subscribe with full list
                    logger.info("Betfair stream: updated subscription (%d markets total)",
                                len(self._market_ids))

                try:
                    msg = await asyncio.wait_for(self._read_line(), timeout=timeout)
                    self._handle_message(msg)
                except asyncio.TimeoutError:
                    # No message within timeout — connection may be dead
                    logger.warning("Betfair stream: heartbeat timeout, reconnecting...")
                    break
        finally:
            self._close()

    async def _send(self, msg: dict):
        """Send a JSON message terminated by CRLF."""
        if self._writer is None:
            return
        line = json.dumps(msg, separators=(",", ":")) + "\r\n"
        self._writer.write(line.encode("utf-8"))
        await self._writer.drain()

    async def _read_line(self) -> dict:
        """Read one CRLF-delimited JSON message from the stream."""
        if self._reader is None:
            raise ConnectionError("Betfair stream: not connected")
        raw = await self._reader.readuntil(b"\r\n")
        return json.loads(raw.strip())

    async def _subscribe(self, market_ids: list[str]):
        """Send a market subscription message.

        Uses the full market_ids list each time (Betfair replaces the
        previous subscription on re-subscribe).
        """
        self._sub_id += 1
        sub_msg = {
            "op": "marketSubscription",
            "id": self._sub_id,
            "marketFilter": {"marketIds": market_ids},
            "marketDataFilter": {
                "fields": ["EX_BEST_OFFERS", "EX_LTP"],
                "ladderLevels": 3,
            },
            "heartbeatMs": self._heartbeat_ms,
        }
        # Include continuation tokens for reconnection if available
        if self._cache.clk:
            sub_msg["clk"] = self._cache.clk
        if self._cache.initial_clk:
            sub_msg["initialClk"] = self._cache.initial_clk
        await self._send(sub_msg)

    def _handle_message(self, msg: dict):
        """Route an incoming stream message to the appropriate handler."""
        op = msg.get("op", "")
        if op == "mcm":
            self._handle_mcm(msg)
        elif op == "status":
            status = msg.get("statusCode", "")
            if status != "SUCCESS":
                logger.warning("Betfair stream status: %s — %s",
                               status, msg.get("errorMessage", ""))
        elif op == "connection":
            pass  # Already handled during connect
        else:
            logger.debug("Betfair stream: unhandled op=%s", op)

    def _handle_mcm(self, msg: dict):
        """Handle a market change message (mcm).

        Updates the cache with market/runner changes and fires the
        on_price_update callback for each affected market.
        """
        if _ws_metrics:
            _ws_metrics.inc("ws_messages_received", {"platform": "betfair"})
        # Store continuation tokens
        self._cache.update_tokens(msg.get("clk"), msg.get("initialClk"))

        for mc in msg.get("mc", []):
            market_id = mc.get("id", "")
            if not market_id:
                continue

            self._cache.apply_market_change(mc)

            # Fire callback for each runner that changed
            changed_selections = set()
            for rc in mc.get("rc", []):
                sel_id = rc.get("id")
                if sel_id is not None:
                    changed_selections.add(sel_id)

            if changed_selections:
                # Build price_data with all runners' current prices
                all_runners = self._cache.get_runners(market_id)
                price_data = {
                    "market_id": market_id,
                    "runners": {},
                }
                for sel_id, prices in all_runners.items():
                    price_data["runners"][sel_id] = prices

                self._on_price_update("betfair", market_id, price_data)

    def _close(self):
        """Close the TCP connection."""
        if self._writer:
            try:
                self._writer.close()
            except Exception as e:
                logger.debug("Error closing Betfair stream: %s", e)
        self._reader = None
        self._writer = None
