"""CLI entry point — argument parsing and initialization."""

import argparse
import io
import logging
import os
import sys
import time

# Fix Windows console encoding for Unicode market names
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

from polymarket_api import (
    fetch_all_markets,
    fetch_events,
    PolymarketTrader,
)
from kalshi_api import KalshiClient
from betfair_api import BetfairClient
from smarkets_api import SmarketsClient
from sxbet_api import SXBetClient
from db import TradeDB
from risk_manager import RiskManager
from executor import ArbitrageExecutor
from notifier import WebhookNotifier
from dashboard import start_dashboard, state as dashboard_state
from display import display_results
from continuous import run_continuous, check_settlements
from scans import (
    scan_binary_internal,
    scan_negrisk_internal,
    scan_cross_platform,
    scan_cross_all,
    scan_kalshi_binary,
    scan_kalshi_multi,
    _fetch_kalshi_data,
    capital_efficiency_score,
    scan_spread_polymarket,
    scan_spread_kalshi,
    scan_betfair_backall,
    scan_betfair_backlay,
    scan_smarkets_backall,
    scan_smarkets_backlay,
    scan_sxbet_backall,
    scan_sxbet_backlay,
)
from config import (
    DEFAULT_MIN_PROFIT,
    MAX_TRADE_SIZE as CONFIG_MAX_TRADE_SIZE,
    DAILY_LOSS_LIMIT as CONFIG_DAILY_LOSS_LIMIT,
    MAX_OPEN_POSITIONS as CONFIG_MAX_OPEN_POSITIONS,
    MIN_LIQUIDITY as CONFIG_MIN_LIQUIDITY,
    MIN_LIQUIDITY_HIGH_ROI as CONFIG_MIN_LIQUIDITY_HIGH_ROI,
    MIN_NET_ROI as CONFIG_MIN_NET_ROI,
    ALLOW_BETTER_REENTRY as CONFIG_ALLOW_BETTER_REENTRY,
    REENTRY_IMPROVEMENT_THRESHOLD as CONFIG_REENTRY_IMPROVEMENT_THRESHOLD,
    RESCAN_INTERVAL as CONFIG_RESCAN_INTERVAL,
    WEBHOOK_URL as CONFIG_WEBHOOK_URL,
    WEBHOOK_MIN_PROFIT as CONFIG_WEBHOOK_MIN_PROFIT,
    DASHBOARD_PORT as CONFIG_DASHBOARD_PORT,
    REVALIDATION_MIN_FLOOR as CONFIG_REVALIDATION_MIN_FLOOR,
    REVALIDATION_ADAPTIVE as CONFIG_REVALIDATION_ADAPTIVE,
    DYNAMIC_SIZING_ENABLED as CONFIG_DYNAMIC_SIZING,
    SIZING_AGGRESSIVENESS as CONFIG_SIZING_AGGRESSIVENESS,
    setup_logging,
)

# Load .env from project dir first, then ~/.claude/.env as fallback
load_dotenv()
load_dotenv(os.path.expanduser("~/.claude/.env"))


def _run_oneshot(args, min_profit, kalshi_client, executor, db, extra_clients=None,
                 notifier=None):
    """One-shot scan mode with parallel data fetching and optional execution."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    extra_clients = extra_clients or {}
    all_opportunities = []

    # Stage 1: Fetch data from all platforms in parallel
    poly_markets = None
    poly_events = None
    kalshi_data = None

    fetch_futures = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        if args.mode not in ("kalshi", "betfair", "smarkets", "sxbet"):
            fetch_futures["poly_markets"] = pool.submit(fetch_all_markets)
        if args.mode in ("all", "negrisk"):
            fetch_futures["poly_events"] = pool.submit(fetch_events)
        if args.mode in ("all", "kalshi", "cross", "spread") and kalshi_client:
            fetch_futures["kalshi_data"] = pool.submit(_fetch_kalshi_data, kalshi_client)

        for key, future in fetch_futures.items():
            try:
                result = future.result()
                if key == "poly_markets":
                    poly_markets = result
                    if poly_markets:
                        logger.info("Fetched %d Polymarket markets.", len(poly_markets))
                    else:
                        logger.warning("Failed to fetch Polymarket markets.")
                elif key == "poly_events":
                    poly_events = result
                    if poly_events:
                        logger.info("Fetched %d events.", len(poly_events))
                elif key == "kalshi_data":
                    kalshi_data = result
            except Exception as e:
                logger.error("Failed to fetch %s: %s", key, e)

    # Stage 2: Run scans in parallel (binary, negrisk, kalshi_binary, kalshi_multi)
    scan_futures = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        if args.mode in ("all", "binary") and poly_markets:
            scan_futures["binary"] = pool.submit(scan_binary_internal, poly_markets, min_profit)
        if args.mode in ("all", "negrisk") and poly_events:
            scan_futures["negrisk"] = pool.submit(scan_negrisk_internal, poly_events, min_profit)
        if args.mode in ("all", "kalshi") and kalshi_client:
            scan_futures["kalshi_binary"] = pool.submit(
                scan_kalshi_binary, kalshi_client, min_profit, kalshi_data=kalshi_data)
            scan_futures["kalshi_multi"] = pool.submit(
                scan_kalshi_multi, kalshi_client, min_profit, kalshi_data=kalshi_data)

        for key, future in scan_futures.items():
            try:
                opps = future.result()
                all_opportunities.extend(opps)
                logger.info("Found %d %s opportunities.", len(opps), key)
            except Exception as e:
                logger.error("Scan %s failed: %s", key, e)

    # Stage 3: Cross-platform scans (need data from stages above)
    kalshi_events_preloaded = kalshi_data[0] if kalshi_data else None

    if args.mode in ("all", "cross"):
        logger.info("--- Cross-Platform Scan (Polymarket vs Kalshi) ---")
        cross_opps = scan_cross_platform(
            poly_markets, kalshi_client, min_profit,
            min_confidence=args.min_confidence,
            kalshi_events_preloaded=kalshi_events_preloaded,
        )
        all_opportunities.extend(cross_opps)
        logger.info("Found %d cross-platform opportunities.", len(cross_opps))

    # Scan cross-all (all platform pairs)
    if args.mode == "cross-all":
        logger.info("--- Cross-All Platform Scan ---")
        platform_clients = {}
        for name, client in extra_clients.items():
            if client:
                logger.info("Fetching %s markets...", name)
                if name == "betfair":
                    events = client.list_events()
                    markets = []
                    for ev in events[:50]:  # Limit for performance
                        ev_data = ev.get("event", {})
                        ev_id = ev_data.get("id", "")
                        if ev_id:
                            mkt_list = client.list_markets(ev_id)
                            markets.extend(mkt_list)
                elif name in ("smarkets", "sxbet"):
                    markets = client.fetch_all_markets()
                else:
                    markets = []
                if markets:
                    logger.info("Fetched %d %s markets.", len(markets), name)
                    platform_clients[name] = (client, markets)

        cross_all_opps = scan_cross_all(
            poly_markets, platform_clients, min_profit,
            min_confidence=args.min_confidence,
        )
        all_opportunities.extend(cross_all_opps)
        logger.info("Found %d cross-all opportunities.", len(cross_all_opps))

    # Stage 4: Platform-specific scans
    if args.mode in ("all", "spread"):
        logger.info("--- Spread Scan ---")
        if poly_markets:
            spread_pm = scan_spread_polymarket(poly_markets, min_profit)
            all_opportunities.extend(spread_pm)
            logger.info("Found %d Polymarket spread opportunities.", len(spread_pm))
        if kalshi_client:
            spread_k = scan_spread_kalshi(kalshi_client, min_profit, kalshi_data=kalshi_data)
            all_opportunities.extend(spread_k)
            logger.info("Found %d Kalshi spread opportunities.", len(spread_k))

    if args.mode in ("all", "betfair"):
        betfair = extra_clients.get("betfair")
        if betfair:
            logger.info("--- Betfair Scan ---")
            bf_backall = scan_betfair_backall(betfair, min_profit)
            all_opportunities.extend(bf_backall)
            logger.info("Found %d Betfair back-all opportunities.", len(bf_backall))
            bf_backlay = scan_betfair_backlay(betfair, min_profit)
            all_opportunities.extend(bf_backlay)
            logger.info("Found %d Betfair back-lay opportunities.", len(bf_backlay))

    if args.mode in ("all", "smarkets"):
        smarkets = extra_clients.get("smarkets")
        if smarkets:
            logger.info("--- Smarkets Scan ---")
            sm_backall = scan_smarkets_backall(smarkets, min_profit)
            all_opportunities.extend(sm_backall)
            logger.info("Found %d Smarkets back-all opportunities.", len(sm_backall))
            sm_backlay = scan_smarkets_backlay(smarkets, min_profit)
            all_opportunities.extend(sm_backlay)
            logger.info("Found %d Smarkets back-lay opportunities.", len(sm_backlay))

    if args.mode in ("all", "sxbet"):
        sxbet = extra_clients.get("sxbet")
        if sxbet:
            logger.info("--- SX Bet Scan ---")
            sx_backall = scan_sxbet_backall(sxbet, min_profit)
            all_opportunities.extend(sx_backall)
            logger.info("Found %d SX Bet back-all opportunities.", len(sx_backall))
            sx_backlay = scan_sxbet_backlay(sxbet, min_profit)
            all_opportunities.extend(sx_backlay)
            logger.info("Found %d SX Bet back-lay opportunities.", len(sx_backlay))

    # Filter by minimum depth if specified
    if args.min_depth > 0:
        before = len(all_opportunities)
        all_opportunities = [
            opp for opp in all_opportunities
            if opp.get("_clob_depth", 0) >= args.min_depth
        ]
        filtered = before - len(all_opportunities)
        if filtered:
            logger.info("Filtered out %d opportunities below min depth %.0f", filtered, args.min_depth)

    # Sort by capital efficiency (ROI * depth) descending
    all_opportunities.sort(key=capital_efficiency_score, reverse=True)

    if args.limit:
        all_opportunities = all_opportunities[:args.limit]

    # Display results
    display_results(all_opportunities, args.json)

    # Send webhook notification
    if notifier and all_opportunities:
        notifier.notify(all_opportunities)

    # Update dashboard state
    dashboard_state.opportunities_found += len(all_opportunities)
    dashboard_state.last_opportunities = all_opportunities[:20]
    dashboard_state.open_positions = db.get_open_positions_count()
    dashboard_state.daily_pnl = db.get_daily_pnl()

    # Execute opportunities if not display-only
    if all_opportunities and (executor.dry_run or executor.exec_mode in ("semi-auto", "full-auto")):
        logger.info("--- Execution Pass ---")
        executed = 0
        for opp in all_opportunities:
            if executor.execute(opp):
                executed += 1
        logger.info("Executed: %d/%d", executed, len(all_opportunities))


def main():
    parser = argparse.ArgumentParser(description="Polymarket Arbitrage Scanner")
    parser.add_argument(
        "--mode",
        choices=["all", "binary", "negrisk", "cross", "kalshi", "cross-all",
                 "spread", "betfair", "smarkets", "sxbet"],
        default="all",
        help="Scan mode: all, binary, negrisk, cross, kalshi, cross-all, spread, betfair, smarkets, sxbet",
    )
    parser.add_argument(
        "--min-profit",
        type=float,
        default=None,
        help="Minimum net profit threshold (0-1, e.g., 0.01 = 1%%)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of results to display",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--min-confidence",
        choices=["HIGH", "MEDIUM", "LOW"],
        default="LOW",
        help="Minimum cross-platform match confidence (default: LOW)",
    )
    parser.add_argument(
        "--min-depth",
        type=float,
        default=0,
        help="Minimum order book depth to display (default: 0 = no filter)",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Run persistently with WebSocket feeds and periodic re-scans",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Seconds between re-scans in continuous mode (default: from RESCAN_INTERVAL env/config)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Detect and log opportunities without executing trades",
    )
    parser.add_argument(
        "--exec-mode",
        choices=["semi-auto", "full-auto"],
        default=None,
        help="Execution mode (default: from .env or semi-auto)",
    )
    parser.add_argument(
        "--max-trade",
        type=float,
        default=None,
        help="Maximum dollar amount per trade (default: from .env or 5.00)",
    )
    parser.add_argument(
        "--webhook",
        type=str,
        default=None,
        help="Webhook URL for opportunity notifications (Slack/Discord/generic)",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=None,
        help="Port for HTTP status dashboard (0 = disabled)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Logging level (default: from .env or INFO)",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Log file path (default: from .env or none)",
    )
    args = parser.parse_args()

    # Configure logging first (before any logger calls)
    setup_logging(level=args.log_level, log_file=args.log_file)

    min_profit = args.min_profit or float(os.getenv("MIN_PROFIT_THRESHOLD", DEFAULT_MIN_PROFIT))

    # Resolve execution settings from CLI > .env > defaults
    dry_run = args.dry_run if args.dry_run is not None else os.getenv("DRY_RUN", "true").lower() == "true"
    exec_mode = args.exec_mode or os.getenv("EXECUTION_MODE", "semi-auto")
    max_trade = args.max_trade or float(os.getenv("MAX_TRADE_SIZE", "5.0"))

    logger.info("=" * 80)
    logger.info("POLYMARKET ARBITRAGE SCANNER v2")
    logger.info("Min profit threshold: %.2f%%", min_profit * 100)
    if args.continuous:
        logger.info("Mode: CONTINUOUS | Exec: %s | Dry-run: %s | Max trade: $%.2f", exec_mode, dry_run, max_trade)
    logger.info("=" * 80)

    # Initialize execution components
    db = TradeDB()
    risk_config = {
        "max_trade_size": max_trade,
        "daily_loss_limit": CONFIG_DAILY_LOSS_LIMIT,
        "max_open_positions": CONFIG_MAX_OPEN_POSITIONS,
        "min_liquidity": CONFIG_MIN_LIQUIDITY,
        "min_liquidity_high_roi": CONFIG_MIN_LIQUIDITY_HIGH_ROI,
        "min_net_roi": CONFIG_MIN_NET_ROI,
        "allow_better_reentry": CONFIG_ALLOW_BETTER_REENTRY,
        "reentry_improvement_threshold": CONFIG_REENTRY_IMPROVEMENT_THRESHOLD,
    }
    risk_manager = RiskManager(risk_config)

    # Initialize platform clients
    kalshi_client = None
    kalshi_api_key_id = os.getenv("KALSHI_API_KEY_ID")
    kalshi_private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH")
    kalshi_private_key_b64 = os.getenv("KALSHI_PRIVATE_KEY_BASE64")
    if kalshi_api_key_id and (kalshi_private_key_path or kalshi_private_key_b64):
        kalshi_client = KalshiClient()
        logger.info("Authenticating with Kalshi (API key)...")
        if kalshi_private_key_b64:
            success = kalshi_client.login_with_api_key(kalshi_api_key_id, private_key_base64=kalshi_private_key_b64)
        else:
            kalshi_private_key_path = os.path.expanduser(kalshi_private_key_path)
            success = kalshi_client.login_with_api_key(kalshi_api_key_id, private_key_path=kalshi_private_key_path)
        if not success:
            kalshi_client = None
            logger.warning("Kalshi auth failed.")
        else:
            logger.info("Kalshi authenticated successfully.")
    else:
        logger.info("KALSHI_API_KEY_ID/KALSHI_PRIVATE_KEY_PATH not set in .env")

    pm_trader = None
    pm_private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    if pm_private_key and not dry_run:
        pm_chain_id = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
        try:
            pm_trader = PolymarketTrader(pm_private_key, pm_chain_id)
            logger.info("Polymarket trader initialized.")
        except Exception as e:
            logger.warning("Polymarket trader init failed: %s", e)

    # Initialize additional platform clients
    betfair_client = None
    smarkets_client = None
    sxbet_client = None

    if args.mode in ("all", "cross-all", "betfair"):
        bf_api_key = os.getenv("BETFAIR_API_KEY")
        bf_user = os.getenv("BETFAIR_USERNAME")
        if bf_api_key and bf_user:
            betfair_client = BetfairClient()
            if not betfair_client.login():
                betfair_client = None
                logger.warning("Betfair auth failed.")
            else:
                logger.info("Betfair authenticated successfully.")

    if args.mode in ("all", "cross-all", "smarkets"):
        sm_api_key = os.getenv("SMARKETS_API_KEY")
        if sm_api_key:
            smarkets_client = SmarketsClient()
            if not smarkets_client.login():
                smarkets_client = None
                logger.warning("Smarkets auth failed.")
            else:
                logger.info("Smarkets authenticated successfully.")

    if args.mode in ("all", "cross-all", "sxbet"):
        sx_api_key = os.getenv("SXBET_API_KEY")
        if sx_api_key:
            sxbet_client = SXBetClient()
            if not sxbet_client.login():
                sxbet_client = None
                logger.warning("SX Bet auth failed.")
            else:
                logger.info("SX Bet authenticated successfully.")

    # Price cache updated by WebSocket feeds (shared with executor for revalidation)
    price_cache = {}

    executor = ArbitrageExecutor(
        pm_trader=pm_trader,
        kalshi_client=kalshi_client,
        db=db,
        risk_manager=risk_manager,
        dry_run=dry_run,
        exec_mode=exec_mode,
        max_trade_size=max_trade,
        price_cache=price_cache,
        betfair_client=betfair_client,
        smarkets_client=smarkets_client,
        sxbet_client=sxbet_client,
        revalidation_adaptive=CONFIG_REVALIDATION_ADAPTIVE,
        revalidation_min_floor=CONFIG_REVALIDATION_MIN_FLOOR,
        dynamic_sizing=CONFIG_DYNAMIC_SIZING,
        sizing_aggressiveness=CONFIG_SIZING_AGGRESSIVENESS,
    )

    extra_clients = {
        "betfair": betfair_client,
        "smarkets": smarkets_client,
        "sxbet": sxbet_client,
    }

    # Initialize webhook notifier
    webhook_url = args.webhook or CONFIG_WEBHOOK_URL
    notifier = None
    if webhook_url:
        notifier = WebhookNotifier(webhook_url, min_profit=CONFIG_WEBHOOK_MIN_PROFIT)
        logger.info("Webhook notifications enabled.")

    # Start dashboard
    dashboard_port = args.dashboard_port if args.dashboard_port is not None else CONFIG_DASHBOARD_PORT
    dashboard_server = start_dashboard(dashboard_port)

    if args.continuous:
        run_continuous(args, min_profit, kalshi_client, kalshi_api_key_id,
                       kalshi_private_key_path, executor, db, price_cache,
                       extra_clients, notifier=notifier, pm_trader=pm_trader)
    else:
        _run_oneshot(args, min_profit, kalshi_client, executor, db, extra_clients,
                     notifier=notifier)

    if dashboard_server:
        dashboard_server.shutdown()
    db.close()


if __name__ == "__main__":
    main()
