"""Continuous mode: periodic re-scans with WebSocket feeds, settlement, and dashboard updates."""

import asyncio
import json
import logging
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from polymarket_api import fetch_all_markets, fetch_events
from ws_feeds import FeedManager
from db import TradeDB
from display import display_results
from dashboard import state as dashboard_state
from recovery import reconcile_orphaned_positions
from fees import (
    net_profit_binary_internal,
    net_profit_negrisk_internal,
    net_profit_cross_platform,
    net_profit_kalshi_binary,
    net_profit_kalshi_multi,
    net_profit_cross_betfair,
    net_profit_cross_generic,
)
from config import (
    RESCAN_INTERVAL as CONFIG_RESCAN_INTERVAL,
    WS_SUBSCRIPTION_LIMIT as CONFIG_WS_SUBSCRIPTION_LIMIT,
    WS_TRIGGER_ENABLED as CONFIG_WS_TRIGGER_ENABLED,
    WS_TRIGGER_THRESHOLD as CONFIG_WS_TRIGGER_THRESHOLD,
    HEDGE_ENABLED as CONFIG_HEDGE_ENABLED,
    SNAPSHOT_ENABLED as CONFIG_SNAPSHOT_ENABLED,
    SNAPSHOT_INTERVAL as CONFIG_SNAPSHOT_INTERVAL,
)

# Conditional metrics import — never breaks if metrics.py is missing
try:
    from config import METRICS_ENABLED as _METRICS_ENABLED
    if _METRICS_ENABLED:
        from metrics import metrics as _metrics
    else:
        _metrics = None
except Exception:
    _metrics = None
from scans import (
    scan_binary_internal,
    scan_negrisk_internal,
    scan_cross_platform,
    scan_cross_all,
    scan_kalshi_binary,
    scan_kalshi_multi,
    scan_spread_polymarket,
    scan_spread_kalshi,
    scan_betfair_backall,
    scan_betfair_backlay,
    scan_smarkets_backall,
    scan_smarkets_backlay,
    scan_sxbet_backall,
    scan_sxbet_backlay,
    scan_matchbook_backall,
    scan_matchbook_backlay,
    scan_gemini_binary,
    scan_gemini_multi,
    scan_ibkr_binary,
    scan_triangular,
    _fetch_kalshi_data,
    capital_efficiency_score,
)

logger = logging.getLogger(__name__)


class OpportunityIndex:
    """Maps (platform, ticker/token) to opportunities for fast lookup on WS updates."""

    def __init__(self):
        self._index: dict[tuple[str, str], list[dict]] = {}
        self._lock = threading.Lock()

    def rebuild(self, opportunities: list[dict]):
        """Rebuild the index from a list of opportunities."""
        new_index: dict[tuple[str, str], list[dict]] = {}
        for opp in opportunities:
            keys = self._extract_keys(opp)
            for key in keys:
                new_index.setdefault(key, []).append(opp)
        with self._lock:
            self._index = new_index

    def lookup(self, platform: str, ticker: str) -> list[dict]:
        """Look up opportunities affected by a price update for (platform, ticker)."""
        with self._lock:
            return list(self._index.get((platform, ticker), []))

    def get_subscription_tokens(self, limit: int = 500) -> tuple[list[str], list[str]]:
        """Get top token IDs for WS subscription, prioritized by opportunity profit.

        Returns (poly_token_ids, kalshi_tickers).
        """
        poly_tokens = set()
        kalshi_tickers = set()
        with self._lock:
            # Sort by profit descending
            scored = []
            for key, opps in self._index.items():
                best_profit = max(o.get("net_profit", 0) for o in opps)
                scored.append((best_profit, key))
            scored.sort(reverse=True)

            for _, (platform, token) in scored:
                if platform == "polymarket" and len(poly_tokens) < limit:
                    poly_tokens.add(token)
                elif platform == "kalshi" and len(kalshi_tickers) < limit:
                    kalshi_tickers.add(token)
        return list(poly_tokens), list(kalshi_tickers)

    @staticmethod
    def _extract_keys(opp: dict) -> list[tuple[str, str]]:
        """Extract (platform, ticker) keys from an opportunity."""
        keys = []
        opp_type = opp.get("type", "")

        # Polymarket token IDs
        token_ids = opp.get("_token_ids", [])
        for tid in token_ids:
            if tid:
                keys.append(("polymarket", tid))

        # Kalshi tickers
        kalshi_ticker = opp.get("_kalshi_ticker", "")
        if kalshi_ticker:
            keys.append(("kalshi", kalshi_ticker))

        kalshi_tickers = opp.get("_kalshi_tickers", [])
        for t in kalshi_tickers:
            if t:
                keys.append(("kalshi", t))

        # Betfair
        bf_market_id = opp.get("_bf_market_id") or opp.get("_market_id", "")
        if bf_market_id and "betfair" in opp_type.lower():
            keys.append(("betfair", bf_market_id))

        # Smarkets
        sm_market_id = opp.get("_sm_market_id", "")
        if sm_market_id:
            keys.append(("smarkets", sm_market_id))

        # SX Bet
        sx_hash = opp.get("_sx_market_hash", "")
        if sx_hash:
            keys.append(("sxbet", sx_hash))

        # Matchbook
        mb_market_id = opp.get("_mb_market_id", "")
        if mb_market_id:
            keys.append(("matchbook", mb_market_id))

        # Gemini
        gm_event_id = opp.get("_gm_event_id", "")
        if gm_event_id:
            keys.append(("gemini", gm_event_id))

        # IBKR
        ibkr_event_id = opp.get("_ibkr_event_id", "")
        if ibkr_event_id:
            keys.append(("ibkr", ibkr_event_id))

        # EventDivergence: index by platform + metaculus question ID
        if opp_type == "EventDivergence":
            platform = opp.get("_platform", "")
            metaculus_id = opp.get("_metaculus_id")
            if platform and metaculus_id:
                keys.append((platform, f"metaculus_{metaculus_id}"))

        # TriangularCross: index by both platform keys
        if opp_type == "TriangularCross":
            for pkey in ("_platform_a", "_platform_b"):
                pname = opp.get(pkey, "")
                if pname:
                    keys.append((pname, opp.get("market", "")))

        return keys


def _calc_realized_pnl(db: TradeDB, pos: dict) -> float:
    """Calculate realized P&L from actual fill prices in the trades table.

    Realized P&L = payout ($1.00) - sum of actual fill costs.
    Falls back to expected_pnl if no fill data available.
    """
    trades = db.get_trades_for_opportunity(pos["opportunity_id"])
    if not trades:
        return pos.get("expected_pnl", 0)
    total_fill_cost = sum(
        (t.get("fill_price") or t["price"]) * t["size"] for t in trades
    )
    if total_fill_cost <= 0:
        return pos.get("expected_pnl", 0)
    return 1.0 - total_fill_cost


def check_settlements(
    db: TradeDB,
    kalshi_client,
    poly_markets: list[dict] | None,
    betfair_client=None,
    smarkets_client=None,
    sxbet_client=None,
    matchbook_client=None,
    gemini_client=None,
    ibkr_client=None,
):
    """Check open positions for settlement and update realised P&L.

    Iterates all open positions in the database, queries each platform's
    API for resolution status, and settles any positions whose underlying
    market has resolved. Calculates realised P&L from trade history and
    updates the position record. Also processes pending partial fills that
    need hedging.

    Args:
        db: TradeDB instance for reading open positions and writing settlements.
        kalshi_client: Authenticated KalshiClient, or None to skip Kalshi
            settlement checks.
        poly_markets: Latest Polymarket markets list (used to check resolution
            status), or None.
        betfair_client: Optional BetfairClient for Betfair settlement checks.
        smarkets_client: Optional SmarketsClient for Smarkets settlement checks.
        sxbet_client: Optional SXBetClient for SX Bet settlement checks.
        matchbook_client: Optional MatchbookClient for Matchbook settlement
            checks.
        gemini_client: Optional GeminiClient for Gemini settlement checks.
        ibkr_client: Optional IBKRClient for IBKR ForecastEx settlement checks.
    """
    open_positions = db.get_open_positions()
    if not open_positions:
        return

    logger.info("Checking %d open positions for settlement...", len(open_positions))
    settled = 0
    for pos in open_positions:
        platform = pos["platform"]
        market_id = pos["market_identifier"]

        try:
            if platform == "kalshi" and kalshi_client:
                resp = kalshi_client._request("GET", f"/markets/{market_id}")
                if resp and resp.status_code == 200:
                    data = resp.json()
                    market_data = data.get("market", data)
                    result = market_data.get("result", "")
                    if result:
                        realized = _calc_realized_pnl(db, pos)
                        db.settle_position(pos["id"], realized_pnl=realized, status="settled")
                        settled += 1
            elif platform in ("polymarket", "cross"):
                try:
                    from polymarket_api import _get_with_retry, GAMMA_BASE
                    resp = _get_with_retry(f"{GAMMA_BASE}/markets/{market_id}", timeout=15)
                    if resp and resp.status_code == 200:
                        pm_data = resp.json()
                        if pm_data.get("closed") or pm_data.get("resolvedOutcome"):
                            realized = _calc_realized_pnl(db, pos)
                            db.settle_position(pos["id"], realized_pnl=realized, status="settled")
                            settled += 1
                except Exception as e:
                    logger.warning("PM settlement check failed for position %s: %s", pos['id'], e)
            elif platform == "betfair" and betfair_client:
                try:
                    from betfair_api import BETFAIR_EXCHANGE_URL, _rate_limit as bf_rate_limit
                    bf_rate_limit()
                    resp = betfair_client.session.post(
                        f"{BETFAIR_EXCHANGE_URL}/listMarketBook/",
                        json={"marketIds": [market_id]},
                        timeout=15,
                    )
                    if resp and resp.status_code == 200:
                        books = resp.json()
                        if books and isinstance(books, list) and books:
                            mkt_status = books[0].get("status", "")
                            if mkt_status in ("CLOSED", "SETTLED"):
                                realized = _calc_realized_pnl(db, pos)
                                db.settle_position(pos["id"], realized_pnl=realized, status="settled")
                                settled += 1
                except Exception as e:
                    logger.warning("Betfair settlement check failed for position %s: %s", pos['id'], e)
            elif platform == "smarkets" and smarkets_client:
                try:
                    market_data = smarkets_client.get_market_status(market_id) if hasattr(smarkets_client, "get_market_status") else None
                    if market_data and market_data.get("state") in ("settled", "closed"):
                        realized = _calc_realized_pnl(db, pos)
                        db.settle_position(pos["id"], realized_pnl=realized, status="settled")
                        settled += 1
                except Exception as e:
                    logger.warning("Smarkets settlement check failed for position %s: %s", pos['id'], e)
            elif platform == "sxbet" and sxbet_client:
                try:
                    market_data = sxbet_client.get_market_status(market_id) if hasattr(sxbet_client, "get_market_status") else None
                    if market_data and market_data.get("status") in ("SETTLED", "CLOSED"):
                        realized = _calc_realized_pnl(db, pos)
                        db.settle_position(pos["id"], realized_pnl=realized, status="settled")
                        settled += 1
                except Exception as e:
                    logger.warning("SX Bet settlement check failed for position %s: %s", pos['id'], e)
            elif platform == "matchbook" and matchbook_client:
                try:
                    market_data = matchbook_client.get_market_status(market_id) if hasattr(matchbook_client, "get_market_status") else None
                    if market_data:
                        event_status = market_data.get("status", "")
                        if event_status in ("settled", "closed", "resulted"):
                            realized = _calc_realized_pnl(db, pos)
                            db.settle_position(pos["id"], realized_pnl=realized, status="settled")
                            settled += 1
                except Exception as e:
                    logger.warning("Matchbook settlement check failed for position %s: %s", pos['id'], e)
            elif platform == "gemini" and gemini_client:
                try:
                    market_data = gemini_client.get_market_status(market_id) if hasattr(gemini_client, "get_market_status") else None
                    if market_data and market_data.get("status") in ("settled", "closed", "resolved"):
                        realized = _calc_realized_pnl(db, pos)
                        db.settle_position(pos["id"], realized_pnl=realized, status="settled")
                        settled += 1
                except Exception as e:
                    logger.warning("Gemini settlement check failed for position %s: %s", pos['id'], e)
            elif platform == "ibkr" and ibkr_client:
                try:
                    market_data = ibkr_client.get_market_status(market_id) if hasattr(ibkr_client, "get_market_status") else None
                    if market_data and market_data.get("status") in ("settled", "closed", "expired"):
                        realized = _calc_realized_pnl(db, pos)
                        db.settle_position(pos["id"], realized_pnl=realized, status="settled")
                        settled += 1
                except Exception as e:
                    logger.warning("IBKR settlement check failed for position %s: %s", pos['id'], e)
        except Exception as e:
            logger.warning("Settlement check failed for position %s: %s", pos['id'], e)

    if settled:
        logger.info("Settled %d positions.", settled)


def _recalc_profit(opp: dict, platform: str, ticker: str, new_price: float, price_cache: dict) -> float | None:
    """Recalculate net profit for an opportunity using a fresh WS price.

    Dispatches to the correct fee function based on opportunity type.
    Returns recalculated net profit, or None if unable to compute.
    """
    opp_type = opp.get("type", "")
    try:
        if opp_type == "Binary":
            # Need both YES and NO prices from cache
            token_ids = opp.get("_token_ids", [])
            if len(token_ids) < 2:
                return None
            prices = []
            for tid in token_ids:
                if tid == ticker:
                    prices.append(new_price)
                else:
                    cached = price_cache.get((platform, tid))
                    if cached and cached.get("price") is not None:
                        prices.append(cached["price"])
                    else:
                        return None
            result = net_profit_binary_internal(prices[0], prices[1])
            return result["net_profit"]
        elif opp_type.startswith("NegRisk"):
            token_ids = opp.get("_token_ids", [])
            if not token_ids:
                return None
            prices = []
            for tid in token_ids:
                if tid == ticker:
                    prices.append(new_price)
                else:
                    cached = price_cache.get((platform, tid))
                    if cached and cached.get("price") is not None:
                        prices.append(cached["price"])
                    else:
                        return None
            result = net_profit_negrisk_internal(prices)
            return result["net_profit"]
        elif opp_type == "KalshiBinary":
            k_ticker = opp.get("_kalshi_ticker", "")
            cached = price_cache.get(("kalshi", k_ticker))
            if not cached:
                return None
            k_yes = cached.get("yes_price", opp.get("_kalshi_yes"))
            k_no = cached.get("no_price", opp.get("_kalshi_no"))
            if k_yes is None or k_no is None:
                return None
            result = net_profit_kalshi_binary(k_yes, k_no)
            return result["net_profit"]
        elif opp_type.startswith("Cross"):
            # Cross-platform: use the WS-updated price for one side and cached
            # price for the other. Requires _price_a/_price_b/_platform_a/_platform_b
            # metadata attached by the scan (added in cross.py).
            pa = opp.get("_platform_a", "")
            pb = opp.get("_platform_b", "")
            price_a = opp.get("_price_a")
            price_b = opp.get("_price_b")
            side_a = opp.get("_side_a", "yes")
            side_b = opp.get("_side_b", "no")
            if price_a is None or price_b is None or not pa or not pb:
                return None

            # Determine which side the WS update applies to
            if platform == pa:
                price_a = new_price
            elif platform == pb:
                price_b = new_price
            else:
                return None  # Update is for an unrelated platform

            result = net_profit_cross_generic(
                price_a, price_b, side_a, side_b,
                platform_a=pa, platform_b=pb,
            )
            return result["net_profit"]
    except Exception as e:
        logger.debug("Error recalculating profit: %s", e)
        return None
    return None


# Per-market lock management for concurrent WS-triggered execution
_market_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _get_market_lock(market: str) -> threading.Lock:
    """Get or create a lock for a specific market."""
    with _locks_lock:
        if market not in _market_locks:
            _market_locks[market] = threading.Lock()
        return _market_locks[market]


def run_continuous(args, min_profit, kalshi_client, kalshi_api_key_id,
                   kalshi_private_key_path, executor, db, price_cache,
                   extra_clients=None, notifier=None, pm_trader=None,
                   event_monitor=None):
    """Run the scanner in continuous mode with WebSocket price feeds.

    Sets up WebSocket connections to all configured platforms, runs periodic
    full re-scans at a configurable interval, and optionally triggers
    immediate execution when a WS price update moves a tracked opportunity
    above the profit threshold. Handles graceful shutdown on SIGINT/SIGTERM,
    periodic settlement checks, stale price eviction, and dashboard state
    updates.

    Args:
        args: Parsed CLI argparse namespace (uses mode, continuous, interval,
            min_confidence, min_depth, limit, json, dry_run, exec_mode,
            max_trade, dashboard_port).
        min_profit: Minimum net profit threshold (0-1 float, e.g. 0.01 = 1%).
        kalshi_client: Authenticated KalshiClient instance, or None.
        kalshi_api_key_id: Kalshi API key ID string for WS auth, or None.
        kalshi_private_key_path: Path to Kalshi RSA private key PEM, or None.
        executor: TradeExecutor instance for opportunity execution.
        db: TradeDB instance for logging and settlement tracking.
        price_cache: Shared dict keyed by (platform, ticker) storing latest
            price snapshots from WebSocket feeds.
        extra_clients: Optional dict of additional platform clients keyed by
            platform name (e.g. {"betfair": BetfairClient, ...}).
        notifier: Optional Notifier instance for webhook/Slack alerts.
        pm_trader: Optional PolymarketTrader for on-chain execution.
        event_monitor: Optional EventMonitor for cross-event divergence
            tracking.
    """
    extra_clients = extra_clients or {}
    rescan_interval = getattr(args, 'interval', None) or CONFIG_RESCAN_INTERVAL

    shutdown_event = asyncio.Event()

    def _signal_handler(sig, frame):
        logger.info("Shutting down gracefully...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    opp_index = OpportunityIndex()
    ws_trigger_enabled = CONFIG_WS_TRIGGER_ENABLED
    ws_trigger_threshold = CONFIG_WS_TRIGGER_THRESHOLD
    ws_sub_limit = CONFIG_WS_SUBSCRIPTION_LIMIT

    def on_price_update(platform, ticker, data):
        data["_ts"] = time.time()
        price_cache[(platform, ticker)] = data

        # Event-driven execution: check if this update affects a tracked opportunity
        if not ws_trigger_enabled:
            return
        affected = opp_index.lookup(platform, ticker)
        if not affected:
            return
        for opp in affected:
            # Recalculate profit using fresh WS price instead of stale value
            cached = price_cache.get((platform, ticker), {})
            new_price = cached.get("price")
            if new_price is None:
                continue
            recalculated_profit = _recalc_profit(opp, platform, ticker, new_price, price_cache)
            profit = recalculated_profit if recalculated_profit is not None else opp.get("net_profit", 0)
            if profit >= ws_trigger_threshold:
                market_name = opp.get("market", "?")
                lock = _get_market_lock(market_name)
                if lock.acquire(blocking=False):
                    try:
                        opp["net_profit"] = profit
                        logger.info("WS trigger: executing %s (profit $%.4f)",
                                    market_name[:30], profit)
                        executor.execute(opp)
                    finally:
                        lock.release()

    def _cleanup_price_cache():
        """Evict price cache entries older than 60 seconds."""
        now = time.time()
        stale_keys = [k for k, v in price_cache.items() if now - v.get("_ts", 0) > 60]
        for k in stale_keys:
            del price_cache[k]
        if stale_keys:
            logger.debug("Evicted %d stale price cache entries.", len(stale_keys))

    # Crash recovery: reconcile orphaned positions from previous session
    reconcile_orphaned_positions(
        db,
        kalshi_client=kalshi_client,
        pm_trader=pm_trader,
        betfair_client=extra_clients.get("betfair"),
        smarkets_client=extra_clients.get("smarkets"),
        sxbet_client=extra_clients.get("sxbet"),
        matchbook_client=extra_clients.get("matchbook"),
        gemini_client=extra_clients.get("gemini"),
        ibkr_client=extra_clients.get("ibkr"),
    )

    # Initialize partial fill hedger for continuous mode
    hedger = None
    if CONFIG_HEDGE_ENABLED:
        from hedger import PartialFillHedger
        hedger = PartialFillHedger(
            pm_trader=pm_trader,
            kalshi_client=kalshi_client,
            betfair_client=extra_clients.get("betfair"),
            smarkets_client=extra_clients.get("smarkets"),
            sxbet_client=extra_clients.get("sxbet"),
            matchbook_client=extra_clients.get("matchbook"),
            gemini_client=extra_clients.get("gemini"),
            db=db,
        )

    # Initialize snapshot recorder for backtesting data collection
    snapshot_recorder = None
    if CONFIG_SNAPSHOT_ENABLED:
        try:
            from snapshot import SnapshotRecorder
            snapshot_recorder = SnapshotRecorder()
            logger.info("Snapshot recording enabled (interval=%ds).", CONFIG_SNAPSHOT_INTERVAL)
        except Exception as e:
            logger.warning("Failed to initialize snapshot recorder: %s", e)

    _last_snapshot_time = 0.0

    # Initialize WebSocket feed manager
    feed_manager = FeedManager(
        on_price_update=on_price_update,
        kalshi_api_key_id=kalshi_api_key_id,
        kalshi_private_key_path=kalshi_private_key_path,
    )

    async def _continuous_loop():
        ws_task = None
        scan_count = 0

        while not shutdown_event.is_set():
            scan_count += 1
            logger.info("=" * 80)
            logger.info("CONTINUOUS SCAN #%d", scan_count)
            logger.info("=" * 80)

            _scan_start = time.time()

            try:
                from concurrent.futures import ThreadPoolExecutor

                # Stage 1: Fetch data from all platforms in parallel
                poly_markets = []
                poly_events = None
                kalshi_data = None

                fetch_futures = {}
                with ThreadPoolExecutor(max_workers=3) as pool:
                    if args.mode not in ("kalshi", "betfair", "smarkets", "sxbet", "matchbook", "gemini", "ibkr", "triangular"):
                        fetch_futures["poly_markets"] = pool.submit(fetch_all_markets)
                    if args.mode in ("all", "negrisk"):
                        fetch_futures["poly_events"] = pool.submit(fetch_events)
                    if args.mode in ("all", "kalshi", "cross", "spread") and kalshi_client:
                        fetch_futures["kalshi_data"] = pool.submit(_fetch_kalshi_data, kalshi_client)

                    for key, future in fetch_futures.items():
                        try:
                            result = future.result()
                            if key == "poly_markets":
                                poly_markets = result or []
                            elif key == "poly_events":
                                poly_events = result
                            elif key == "kalshi_data":
                                kalshi_data = result
                        except Exception as e:
                            logger.error("Failed to fetch %s: %s", key, e)

                all_opportunities = []

                # Stage 2: Run scans in parallel
                scan_futures = {}
                with ThreadPoolExecutor(max_workers=4) as pool:
                    if args.mode in ("all", "binary") and poly_markets:
                        scan_futures["binary"] = pool.submit(
                            scan_binary_internal, poly_markets, min_profit)
                    if args.mode in ("all", "negrisk") and poly_events:
                        scan_futures["negrisk"] = pool.submit(
                            scan_negrisk_internal, poly_events, min_profit)
                    if args.mode in ("all", "kalshi") and kalshi_client:
                        scan_futures["kalshi_binary"] = pool.submit(
                            scan_kalshi_binary, kalshi_client, min_profit, kalshi_data=kalshi_data)
                        scan_futures["kalshi_multi"] = pool.submit(
                            scan_kalshi_multi, kalshi_client, min_profit, kalshi_data=kalshi_data)

                    for key, future in scan_futures.items():
                        try:
                            opps = future.result()
                            all_opportunities.extend(opps)
                        except Exception as e:
                            logger.error("Scan %s failed: %s", key, e)

                # Stage 3: Cross-platform scans (need data from above)
                kalshi_events_preloaded = kalshi_data[0] if kalshi_data else None

                if args.mode in ("all", "cross"):
                    cross_opps = scan_cross_platform(
                        poly_markets, kalshi_client, min_profit,
                        min_confidence=args.min_confidence,
                        kalshi_events_preloaded=kalshi_events_preloaded,
                    )
                    all_opportunities.extend(cross_opps)

                if args.mode == "cross-all":
                    platform_clients = {}
                    for name, client in extra_clients.items():
                        if client:
                            try:
                                if name == "betfair":
                                    events = client.list_events()
                                    markets = []
                                    for ev in events[:50]:
                                        ev_data = ev.get("event", {})
                                        ev_id = ev_data.get("id", "")
                                        if ev_id:
                                            mkt_list = client.list_markets(ev_id)
                                            markets.extend(mkt_list)
                                elif name in ("smarkets", "sxbet", "matchbook", "gemini", "ibkr"):
                                    markets = client.fetch_all_markets()
                                else:
                                    markets = []
                                if markets:
                                    platform_clients[name] = (client, markets)
                            except Exception as e:
                                logger.warning("Failed to fetch %s markets: %s", name, e)

                    cross_all_opps = scan_cross_all(
                        poly_markets, platform_clients, min_profit,
                        min_confidence=args.min_confidence,
                    )
                    all_opportunities.extend(cross_all_opps)

                # Stage 4: Platform-specific scans (spread, betfair, etc.)
                if args.mode in ("all", "spread"):
                    if poly_markets:
                        spread_pm = scan_spread_polymarket(poly_markets, min_profit)
                        all_opportunities.extend(spread_pm)
                    if kalshi_client:
                        spread_k = scan_spread_kalshi(
                            kalshi_client, min_profit, kalshi_data=kalshi_data)
                        all_opportunities.extend(spread_k)

                if args.mode in ("all", "betfair"):
                    betfair = extra_clients.get("betfair")
                    if betfair:
                        bf_backall = scan_betfair_backall(betfair, min_profit)
                        all_opportunities.extend(bf_backall)
                        bf_backlay = scan_betfair_backlay(betfair, min_profit)
                        all_opportunities.extend(bf_backlay)

                if args.mode in ("all", "smarkets"):
                    smarkets = extra_clients.get("smarkets")
                    if smarkets:
                        sm_backall = scan_smarkets_backall(smarkets, min_profit)
                        all_opportunities.extend(sm_backall)
                        sm_backlay = scan_smarkets_backlay(smarkets, min_profit)
                        all_opportunities.extend(sm_backlay)

                if args.mode in ("all", "sxbet"):
                    sxbet = extra_clients.get("sxbet")
                    if sxbet:
                        sx_backall = scan_sxbet_backall(sxbet, min_profit)
                        all_opportunities.extend(sx_backall)
                        sx_backlay = scan_sxbet_backlay(sxbet, min_profit)
                        all_opportunities.extend(sx_backlay)

                if args.mode in ("all", "matchbook"):
                    matchbook = extra_clients.get("matchbook")
                    if matchbook:
                        mb_backall = scan_matchbook_backall(matchbook, min_profit)
                        all_opportunities.extend(mb_backall)
                        mb_backlay = scan_matchbook_backlay(matchbook, min_profit)
                        all_opportunities.extend(mb_backlay)

                if args.mode in ("all", "gemini"):
                    gemini = extra_clients.get("gemini")
                    if gemini:
                        gm_binary = scan_gemini_binary(gemini, min_profit)
                        all_opportunities.extend(gm_binary)
                        gm_multi = scan_gemini_multi(gemini, min_profit)
                        all_opportunities.extend(gm_multi)

                if args.mode in ("all", "ibkr"):
                    ibkr = extra_clients.get("ibkr")
                    if ibkr:
                        ibkr_binary = scan_ibkr_binary(ibkr, min_profit)
                        all_opportunities.extend(ibkr_binary)

                if args.mode in ("all", "event") and event_monitor:
                    platform_markets_for_event = {}
                    if poly_markets:
                        platform_markets_for_event["polymarket"] = poly_markets
                    if kalshi_data and kalshi_data[0]:
                        platform_markets_for_event["kalshi"] = kalshi_data[0]
                    if platform_markets_for_event:
                        event_opps = event_monitor.scan_event_divergences(
                            platform_markets_for_event, min_profit=min_profit)
                        all_opportunities.extend(event_opps)

                if args.mode in ("all", "triangular"):
                    platform_markets_for_tri = {}
                    platform_clients_for_tri = {}
                    if poly_markets:
                        platform_markets_for_tri["polymarket"] = poly_markets
                    if kalshi_data and kalshi_data[0]:
                        platform_markets_for_tri["kalshi"] = kalshi_data[0]
                        platform_clients_for_tri["kalshi"] = kalshi_client
                    for name, client in extra_clients.items():
                        if client:
                            try:
                                if name == "betfair":
                                    events = client.list_events()
                                    markets = []
                                    for ev in events[:50]:
                                        ev_data = ev.get("event", {})
                                        ev_id = ev_data.get("id", "")
                                        if ev_id:
                                            mkt_list = client.list_markets(ev_id)
                                            markets.extend(mkt_list)
                                elif name in ("smarkets", "sxbet", "matchbook", "gemini", "ibkr"):
                                    markets = client.fetch_all_markets()
                                else:
                                    markets = []
                                if markets:
                                    platform_markets_for_tri[name] = markets
                                    platform_clients_for_tri[name] = client
                            except Exception as e:
                                logger.warning("Triangular: failed to fetch %s: %s", name, e)
                    tri_opps = scan_triangular(
                        platform_markets_for_tri, platform_clients_for_tri, min_profit,
                        min_confidence=args.min_confidence,
                    )
                    all_opportunities.extend(tri_opps)

                # Apply filters
                if args.min_depth > 0:
                    all_opportunities = [
                        opp for opp in all_opportunities
                        if opp.get("_clob_depth", 0) >= args.min_depth
                    ]

                all_opportunities.sort(key=capital_efficiency_score, reverse=True)

                if args.limit:
                    all_opportunities = all_opportunities[:args.limit]

                display_results(all_opportunities, args.json)

                # Send webhook notification
                if notifier and all_opportunities:
                    notifier.notify(all_opportunities)

                # Update dashboard state
                dashboard_state.scan_count = scan_count
                dashboard_state.last_scan_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                dashboard_state.opportunities_found += len(all_opportunities)
                dashboard_state.last_opportunities = all_opportunities[:20]
                dashboard_state.open_positions = db.get_open_positions_count()
                dashboard_state.daily_pnl = db.get_daily_pnl()
                dashboard_state.ws_connections = (1 if ws_task and not ws_task.done() else 0)

                # Update metrics
                if _metrics:
                    _scan_duration = time.time() - _scan_start
                    _metrics.inc("scans_total")
                    _metrics.inc("opportunities_found", value=len(all_opportunities))
                    _metrics.observe("scan_duration_seconds", value=_scan_duration)
                    _metrics.set("scan_cycle_duration_seconds", value=_scan_duration)
                    _metrics.set("active_positions", value=dashboard_state.open_positions)
                    _metrics.set("daily_pnl", value=dashboard_state.daily_pnl)
                    if all_opportunities:
                        best_roi_str = all_opportunities[0].get("net_roi", "0%")
                        try:
                            best_roi = float(best_roi_str.replace("%", "")) / 100 if isinstance(best_roi_str, str) else float(best_roi_str)
                        except (ValueError, TypeError):
                            best_roi = 0
                        _metrics.set("best_opportunity_roi", value=best_roi)
                        for opp in all_opportunities:
                            _metrics.observe("opportunity_profit", value=opp.get("net_profit", 0))
                    _metrics.set("ws_connected", {"platform": "combined"},
                                 value=1 if ws_task and not ws_task.done() else 0)

                # Evict stale price cache entries
                _cleanup_price_cache()

                # Check for settled positions
                check_settlements(
                    db, kalshi_client, poly_markets,
                    betfair_client=extra_clients.get("betfair"),
                    smarkets_client=extra_clients.get("smarkets"),
                    sxbet_client=extra_clients.get("sxbet"),
                    matchbook_client=extra_clients.get("matchbook"),
                    gemini_client=extra_clients.get("gemini"),
                    ibkr_client=extra_clients.get("ibkr"),
                )

                # Execute opportunities sequentially (balance must be rechecked between trades)
                if all_opportunities:
                    logger.info("--- Execution Pass ---")
                    executed = 0
                    for opp in all_opportunities:
                        if shutdown_event.is_set():
                            break
                        try:
                            if executor.execute(opp):
                                executed += 1
                        except Exception as e:
                            logger.error("Execution error: %s", e)
                    logger.info("Executed: %d/%d", executed, len(all_opportunities))

                # Process any pending hedges from partial fills
                if hedger:
                    try:
                        hedger.process_pending_hedges()
                    except Exception as e:
                        logger.warning("Hedger processing failed: %s", e)

                # Record price snapshots for backtesting
                if snapshot_recorder and all_opportunities:
                    nonlocal _last_snapshot_time
                    now = time.time()
                    if now - _last_snapshot_time >= CONFIG_SNAPSHOT_INTERVAL:
                        try:
                            recorded = snapshot_recorder.record_snapshot(all_opportunities)
                            if recorded:
                                logger.debug("Recorded %d snapshots.", recorded)
                            _last_snapshot_time = now
                        except Exception as e:
                            logger.warning("Snapshot recording failed: %s", e)

                # Rebuild opportunity index for WS-triggered execution
                opp_index.rebuild(all_opportunities)

                # Subscribe to WebSocket feeds for discovered markets.
                # We subscribe to opportunity tokens AND also to broader
                # market tokens from cross-platform matched pairs so we
                # can detect arbs that appear between scan cycles.
                poly_sub_ids, kalshi_sub_tickers = opp_index.get_subscription_tokens(ws_sub_limit)

                # Broaden subscriptions: include top cross-platform matched
                # Kalshi tickers and Polymarket tokens even if no arb exists
                # yet.  This lets the WS trigger fire when prices move into
                # profitable range between polling scans.
                if poly_markets:
                    for pm in poly_markets[:ws_sub_limit]:
                        for tok in pm.get("tokens", []):
                            tid = tok.get("token_id", "")
                            if tid and tid not in poly_sub_ids:
                                poly_sub_ids.append(tid)
                        if len(poly_sub_ids) >= ws_sub_limit:
                            break

                # Extract individual market tickers from Kalshi data.
                # kalshi_data is (events, markets_by_event, event_titles).
                # WS needs market tickers (e.g. KXBTCD-...), not event tickers.
                if kalshi_data and len(kalshi_data) >= 2 and kalshi_data[1]:
                    for _evt_ticker, _markets in kalshi_data[1].items():
                        for km in _markets:
                            kt = km.get("ticker", "")
                            if kt and kt not in kalshi_sub_tickers:
                                kalshi_sub_tickers.append(kt)
                            if len(kalshi_sub_tickers) >= ws_sub_limit:
                                break
                        if len(kalshi_sub_tickers) >= ws_sub_limit:
                            break

                if scan_count == 1 and not ws_task:
                    feed_manager.subscribe_polymarket(poly_sub_ids)
                    if kalshi_client:
                        feed_manager.subscribe_kalshi(kalshi_sub_tickers)
                    ws_task = asyncio.create_task(feed_manager.run())
                    logger.info(
                        "WS feeds started: %d Polymarket tokens, %d Kalshi tickers",
                        len(poly_sub_ids), len(kalshi_sub_tickers),
                    )
                elif ws_task and not ws_task.done():
                    feed_manager.update_subscriptions(
                        poly_token_ids=poly_sub_ids,
                        kalshi_tickers=kalshi_sub_tickers,
                    )

            except Exception as e:
                import traceback
                logger.error("Scan failed: %s\n%s", e, traceback.format_exc())
                if _metrics:
                    _metrics.inc("scans_total", {"status": "failed"})

            # Wait for next scan interval or shutdown
            logger.info("Next scan in %ds (Ctrl+C to stop)...", rescan_interval)
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=rescan_interval)
            except asyncio.TimeoutError:
                pass

        # Cleanup
        logger.info("Stopping WebSocket feeds...")
        feed_manager.stop()
        if ws_task:
            ws_task.cancel()
            try:
                await ws_task
            except (asyncio.CancelledError, Exception):
                pass
        logger.info("Shutdown complete.")

    asyncio.run(_continuous_loop())
