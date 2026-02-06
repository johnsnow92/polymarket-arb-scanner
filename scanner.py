#!/usr/bin/env python3
"""Polymarket Arbitrage Scanner.

Scans for three types of arbitrage opportunities:
1. Binary internal (YES + NO < $1.00 on Polymarket)
2. NegRisk internal (sum of all YES prices < $1.00 on multi-outcome markets)
3. Cross-platform (Polymarket vs Kalshi price discrepancies)

Supports one-shot (default) and continuous mode with optional trade execution.
"""

import argparse
import asyncio
import io
import json
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Fix Windows console encoding for Unicode market names
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv
from tabulate import tabulate

from polymarket_api import (
    fetch_all_markets,
    fetch_events,
    get_binary_markets,
    get_clob_prices,
    get_negrisk_events,
    parse_outcome_prices,
    PolymarketTrader,
)
from kalshi_api import KalshiClient
from matcher import match_markets_to_events, detect_inverted
from fees import (
    net_profit_binary_internal,
    net_profit_negrisk_internal,
    net_profit_cross_platform,
)
from db import TradeDB
from risk_manager import RiskManager
from executor import ArbitrageExecutor
from ws_feeds import FeedManager

# Load .env from project dir first, then ~/.claude/.env as fallback
load_dotenv()
load_dotenv(os.path.expanduser("~/.claude/.env"))

DEFAULT_MIN_PROFIT = 0.005  # 0.5% minimum net profit threshold


def _parallel_fetch_kalshi(kalshi_client: KalshiClient, tickers: list[str], max_workers: int = 4) -> dict:
    """Pre-fetch Kalshi markets for multiple event tickers in parallel."""
    results = {}
    if not tickers:
        return results

    unique_tickers = list(set(t for t in tickers if t))
    print(f"  Fetching Kalshi markets for {len(unique_tickers)} events (parallel, {max_workers} workers)...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(kalshi_client.fetch_markets_for_event, t): t
            for t in unique_tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                results[ticker] = future.result()
            except Exception as e:
                print(f"  [WARN] Failed to fetch Kalshi markets for {ticker}: {e}")
                results[ticker] = []

    return results


def _refine_binary_with_clob(opportunities: list[dict], markets_by_question: dict, min_profit: float) -> list[dict]:
    """Stage 2: Re-check binary candidates using CLOB ask prices (what you'd actually pay)."""
    if not opportunities:
        return opportunities

    print(f"  Refining {len(opportunities)} candidates with CLOB ask prices...")
    refined = []
    for opp in opportunities:
        market_key = opp.get("_market_key")
        market = markets_by_question.get(market_key) if market_key else None
        if not market:
            refined.append(opp)
            continue

        clob = get_clob_prices(market)
        if not clob or clob["yes_ask"] is None or clob["no_ask"] is None:
            refined.append(opp)  # Keep if CLOB unavailable
            continue

        yes_ask = clob["yes_ask"]
        no_ask = clob["no_ask"]
        result = net_profit_binary_internal(yes_ask, no_ask)

        if result["net_profit"] >= min_profit:
            # Update with real CLOB prices
            opp["prices"] = f"Y={yes_ask:.3f} N={no_ask:.3f}"
            opp["total_cost"] = f"${yes_ask + no_ask:.4f}"
            opp["gross_spread"] = f"{result['gross_spread']:.4f}"
            opp["fees"] = f"${result['fees']:.4f}"
            opp["net_profit"] = result["net_profit"]
            opp["net_roi"] = f"{result['net_profit'] / (yes_ask + no_ask) * 100:.2f}%"
            # Store depth info for Step 7
            opp["_clob_depth"] = min(
                clob["yes_ask_size"] or 0,
                clob["no_ask_size"] or 0,
            )
            refined.append(opp)

    dropped = len(opportunities) - len(refined)
    if dropped:
        print(f"  Dropped {dropped} candidates at CLOB ask prices.")
    return refined


def scan_binary_internal(markets: list[dict], min_profit: float) -> list[dict]:
    """Scan for binary arbitrage on Polymarket (YES + NO < $1.00)."""
    opportunities = []
    markets_by_question = {}

    binary_markets = get_binary_markets(markets)
    print(f"  Scanning {len(binary_markets)} binary markets...")

    for m in binary_markets:
        prices = parse_outcome_prices(m)
        if not prices or len(prices) != 2:
            continue

        yes_price, no_price = prices[0], prices[1]

        # Skip markets with no liquidity (essentially zero)
        if yes_price <= 0.001 or no_price <= 0.001:
            continue
        # Skip resolved markets (one side near 1.0 and total near 1.0)
        if (yes_price >= 0.99 or no_price >= 0.99) and (yes_price + no_price) > 0.98:
            continue

        result = net_profit_binary_internal(yes_price, no_price)

        if result["net_profit"] >= min_profit:
            market_key = m.get("conditionId", m.get("question", ""))
            markets_by_question[market_key] = m
            opportunities.append({
                "type": "Binary",
                "market": m.get("question", m.get("title", "Unknown"))[:60],
                "prices": f"Y={yes_price:.3f} N={no_price:.3f}",
                "total_cost": f"${yes_price + no_price:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / (yes_price + no_price) * 100:.2f}%",
                "volume": f"${float(m.get('volume', 0) or 0):,.0f}",
                "_market_key": market_key,
            })

    # Stage 2: Refine with CLOB ask prices
    opportunities = _refine_binary_with_clob(opportunities, markets_by_question, min_profit)

    return opportunities


def _refine_negrisk_with_clob(opportunities: list[dict], events_by_title: dict, min_profit: float) -> list[dict]:
    """Stage 2: Re-check NegRisk candidates using CLOB ask prices."""
    if not opportunities:
        return opportunities

    print(f"  Refining {len(opportunities)} NegRisk candidates with CLOB ask prices...")
    refined = []
    for opp in opportunities:
        event_key = opp.get("_event_key")
        event = events_by_title.get(event_key) if event_key else None
        if not event:
            refined.append(opp)
            continue

        markets = event.get("markets", [])
        yes_asks = []
        min_depth = float("inf")
        all_clob_ok = True

        for m in markets:
            clob = get_clob_prices(m)
            if not clob or clob["yes_ask"] is None:
                all_clob_ok = False
                break
            yes_asks.append(clob["yes_ask"])
            depth = clob["yes_ask_size"] or 0
            min_depth = min(min_depth, depth)

        if not all_clob_ok:
            refined.append(opp)  # Keep if CLOB unavailable
            continue

        result = net_profit_negrisk_internal(yes_asks)
        if result["net_profit"] >= min_profit:
            total = sum(yes_asks)
            price_summary = ", ".join(f"{p:.3f}" for p in sorted(yes_asks, reverse=True)[:5])
            if len(yes_asks) > 5:
                price_summary += f"... ({len(yes_asks)} total)"
            opp["prices"] = price_summary
            opp["total_cost"] = f"${total:.4f}"
            opp["gross_spread"] = f"{result['gross_spread']:.4f}"
            opp["fees"] = f"${result['fees']:.4f}"
            opp["net_profit"] = result["net_profit"]
            opp["net_roi"] = f"{result['net_profit'] / total * 100:.2f}%"
            opp["_clob_depth"] = min_depth if min_depth != float("inf") else 0
            refined.append(opp)

    dropped = len(opportunities) - len(refined)
    if dropped:
        print(f"  Dropped {dropped} NegRisk candidates at CLOB ask prices.")
    return refined


def scan_negrisk_internal(events: list[dict], min_profit: float) -> list[dict]:
    """Scan for NegRisk arbitrage on Polymarket multi-outcome events."""
    opportunities = []
    events_by_title = {}

    negrisk_events = get_negrisk_events(events)
    print(f"  Scanning {len(negrisk_events)} NegRisk events...")

    for event in negrisk_events:
        markets = event.get("markets", [])
        if len(markets) < 2:
            continue

        # Collect YES prices for each outcome
        yes_prices = []
        outcome_labels = []
        valid = True

        for m in markets:
            prices = parse_outcome_prices(m)
            if not prices:
                valid = False
                break
            # For negRisk markets, first price is the YES price for that outcome
            yes_price = prices[0]
            if yes_price <= 0:
                valid = False
                break
            yes_prices.append(yes_price)
            label = m.get("groupItemTitle", m.get("question", "?"))
            outcome_labels.append(label[:20])

        if not valid or not yes_prices:
            continue

        # Sanity check: very low total with many outcomes likely means missing markets
        total_yes = sum(yes_prices)
        if len(yes_prices) >= 5 and total_yes < 0.50:
            event_title = event.get("title", "Unknown")[:60]
            print(f"  [WARN] Likely missing outcomes: '{event_title}' "
                  f"({len(yes_prices)} outcomes sum to {total_yes:.3f})")

        result = net_profit_negrisk_internal(yes_prices)

        if result["net_profit"] >= min_profit:
            total = sum(yes_prices)
            price_summary = ", ".join(f"{p:.3f}" for p in sorted(yes_prices, reverse=True)[:5])
            if len(yes_prices) > 5:
                price_summary += f"... ({len(yes_prices)} total)"

            event_key = event.get("id", event.get("title", ""))
            events_by_title[event_key] = event
            opportunities.append({
                "type": f"NegRisk({len(yes_prices)})",
                "market": event.get("title", "Unknown")[:60],
                "prices": price_summary,
                "total_cost": f"${total:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total * 100:.2f}%",
                "volume": f"${sum(float(m.get('volume', 0) or 0) for m in markets):,.0f}",
                "_event_key": event_key,
            })

    # Stage 2: Refine with CLOB ask prices
    opportunities = _refine_negrisk_with_clob(opportunities, events_by_title, min_profit)

    return opportunities


def _refine_cross_with_clob(opportunities: list[dict], markets_by_key: dict, min_profit: float) -> list[dict]:
    """Stage 2: Re-check cross-platform candidates using CLOB ask prices for Polymarket side."""
    if not opportunities:
        return opportunities

    print(f"  Refining {len(opportunities)} cross-platform candidates with CLOB ask prices...")
    refined = []
    for opp in opportunities:
        market_key = opp.get("_market_key")
        market = markets_by_key.get(market_key) if market_key else None
        if not market:
            refined.append(opp)
            continue

        clob = get_clob_prices(market)
        if not clob or clob["yes_ask"] is None or clob["no_ask"] is None:
            refined.append(opp)  # Keep if CLOB unavailable
            continue

        # Re-evaluate with actual ask prices
        pm_yes = clob["yes_ask"]
        pm_no = clob["no_ask"]
        k_yes = opp.get("_kalshi_yes")
        k_no = opp.get("_kalshi_no")

        if k_yes is None or k_no is None:
            refined.append(opp)
            continue

        result1 = net_profit_cross_platform(pm_yes, k_no, "yes", "no")
        result2 = net_profit_cross_platform(pm_no, k_yes, "no", "yes")
        best = result1 if result1["net_profit"] > result2["net_profit"] else result2

        if best["net_profit"] >= min_profit:
            if best == result1:
                total_cost = pm_yes + k_no
                opp["prices"] = f"PM_Y={pm_yes:.3f} K_N={k_no:.3f}"
            else:
                total_cost = pm_no + k_yes
                opp["prices"] = f"PM_N={pm_no:.3f} K_Y={k_yes:.3f}"
            opp["total_cost"] = f"${total_cost:.4f}"
            opp["gross_spread"] = f"{best['gross_spread']:.4f}"
            opp["fees"] = f"${best['fees']:.4f}"
            opp["net_profit"] = best["net_profit"]
            opp["net_roi"] = f"{best['net_profit'] / total_cost * 100:.2f}%"
            opp["_clob_depth"] = min(
                clob["yes_ask_size"] or 0,
                clob["no_ask_size"] or 0,
            )
            refined.append(opp)

    dropped = len(opportunities) - len(refined)
    if dropped:
        print(f"  Dropped {dropped} cross-platform candidates at CLOB ask prices.")
    return refined


def scan_cross_platform(
    poly_markets: list[dict],
    kalshi_client: KalshiClient | None,
    min_profit: float,
    kalshi_markets_by_event: dict | None = None,
    min_confidence: str = "LOW",
) -> list[dict]:
    """Scan for cross-platform arbitrage between Polymarket and Kalshi.

    Strategy: Match Polymarket binary markets to Kalshi events by title,
    then fetch Kalshi markets per matched event for pricing comparison.

    If kalshi_markets_by_event is provided, skip per-event fetches (pre-fetched in parallel).
    """
    opportunities = []
    markets_by_key = {}

    if not kalshi_client:
        print("  [SKIP] Kalshi credentials not configured. Skipping cross-platform scan.")
        return opportunities

    print("  Fetching Kalshi events...")
    kalshi_events = kalshi_client.fetch_all_events()
    if not kalshi_events:
        print("  [WARN] No Kalshi events fetched.")
        return opportunities

    # Filter Polymarket to binary markets only for cross-platform matching
    binary_poly = get_binary_markets(poly_markets)
    print(f"  Matching {len(binary_poly)} Polymarket binary markets vs {len(kalshi_events)} Kalshi events...")

    matched = match_markets_to_events(binary_poly, kalshi_events, threshold=80, min_confidence=min_confidence)
    print(f"  Found {len(matched)} event matches. Fetching Kalshi market prices...")

    # Pre-fetch Kalshi markets in parallel if not already done
    if kalshi_markets_by_event is None:
        tickers = [m["kalshi_event"].get("event_ticker", "") for m in matched if m["kalshi_event"].get("event_ticker")]
        kalshi_markets_by_event = _parallel_fetch_kalshi(kalshi_client, tickers)

    for i, match in enumerate(matched):
        pm = match["polymarket"]
        ke = match["kalshi_event"]

        # Get Polymarket prices
        pm_prices = parse_outcome_prices(pm)
        if not pm_prices or len(pm_prices) != 2:
            continue
        pm_yes, pm_no = pm_prices[0], pm_prices[1]

        # Use pre-fetched Kalshi markets
        event_ticker = ke.get("event_ticker", "")
        k_markets = kalshi_markets_by_event.get(event_ticker, [])
        if not k_markets:
            continue

        # Find the best opportunity across all Kalshi sub-markets in this event
        best_opp = None

        for km in k_markets:
            k_yes, k_no = kalshi_client.get_market_price(km)
            if k_yes is None or k_no is None:
                continue

            # Check for inversion
            pm_title = pm.get("question", pm.get("title", ""))
            k_title = km.get("title", "")
            inverted = detect_inverted(pm_title, k_title)
            if inverted:
                k_yes, k_no = k_no, k_yes

            # Strategy 1: Buy PM YES + Kalshi NO
            result1 = net_profit_cross_platform(pm_yes, k_no, "yes", "no")
            # Strategy 2: Buy PM NO + Kalshi YES
            result2 = net_profit_cross_platform(pm_no, k_yes, "no", "yes")

            best = result1 if result1["net_profit"] > result2["net_profit"] else result2
            if best == result1:
                strategy = "PM_YES + K_NO"
                total_cost = pm_yes + k_no
                prices_str = f"PM_Y={pm_yes:.3f} K_N={k_no:.3f}"
                best_k_yes, best_k_no = k_yes, k_no
            else:
                strategy = "PM_NO + K_YES"
                total_cost = pm_no + k_yes
                prices_str = f"PM_N={pm_no:.3f} K_Y={k_yes:.3f}"
                best_k_yes, best_k_no = k_yes, k_no

            if best["net_profit"] >= min_profit and total_cost > 0:
                if best_opp is None or best["net_profit"] > best_opp["net_profit"]:
                    sim = match["similarity"]
                    market_key = pm.get("conditionId", pm.get("question", ""))
                    markets_by_key[market_key] = pm
                    best_opp = {
                        "type": f"Cross({strategy})",
                        "market": pm_title[:50],
                        "kalshi": k_title[:50],
                        "match": f"{sim}%",
                        "prices": prices_str,
                        "total_cost": f"${total_cost:.4f}",
                        "gross_spread": f"{best['gross_spread']:.4f}",
                        "fees": f"${best['fees']:.4f}",
                        "net_profit": best["net_profit"],
                        "net_roi": f"{best['net_profit'] / total_cost * 100:.2f}%",
                        "volume": f"${float(pm.get('volume', 0) or 0):,.0f}",
                        "_market_key": market_key,
                        "_kalshi_yes": best_k_yes,
                        "_kalshi_no": best_k_no,
                        "confidence": match.get("confidence", "LOW"),
                    }

        if best_opp:
            opportunities.append(best_opp)

        if (i + 1) % 50 == 0:
            print(f"    Processed {i + 1}/{len(matched)} matches...")

    # Stage 2: Refine with CLOB ask prices
    opportunities = _refine_cross_with_clob(opportunities, markets_by_key, min_profit)

    return opportunities


def main():
    parser = argparse.ArgumentParser(description="Polymarket Arbitrage Scanner")
    parser.add_argument(
        "--mode",
        choices=["all", "binary", "negrisk", "cross"],
        default="all",
        help="Scan mode (default: all)",
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
    args = parser.parse_args()

    min_profit = args.min_profit or float(os.getenv("MIN_PROFIT_THRESHOLD", DEFAULT_MIN_PROFIT))

    # Resolve execution settings from CLI > .env > defaults
    dry_run = args.dry_run if args.dry_run is not None else os.getenv("DRY_RUN", "true").lower() == "true"
    exec_mode = args.exec_mode or os.getenv("EXECUTION_MODE", "semi-auto")
    max_trade = args.max_trade or float(os.getenv("MAX_TRADE_SIZE", "5.0"))

    print("=" * 80)
    print("  POLYMARKET ARBITRAGE SCANNER v2")
    print(f"  Min profit threshold: {min_profit * 100:.2f}%")
    if args.continuous:
        print(f"  Mode: CONTINUOUS | Exec: {exec_mode} | Dry-run: {dry_run} | Max trade: ${max_trade:.2f}")
    print("=" * 80)

    # Initialize execution components
    db = TradeDB()
    risk_config = {
        "max_trade_size": max_trade,
        "daily_loss_limit": float(os.getenv("DAILY_LOSS_LIMIT", "25.0")),
        "max_open_positions": int(os.getenv("MAX_OPEN_POSITIONS", "10")),
        "min_liquidity": float(os.getenv("MIN_LIQUIDITY", "50.0")),
        "min_net_roi": float(os.getenv("MIN_NET_ROI", "0.01")),
    }
    risk_manager = RiskManager(risk_config)

    # Initialize platform clients
    kalshi_client = None
    kalshi_api_key_id = os.getenv("KALSHI_API_KEY_ID")
    kalshi_private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH")
    if kalshi_api_key_id and kalshi_private_key_path:
        kalshi_private_key_path = os.path.expanduser(kalshi_private_key_path)
        kalshi_client = KalshiClient()
        print("\n  Authenticating with Kalshi (API key)...")
        if not kalshi_client.login_with_api_key(kalshi_api_key_id, kalshi_private_key_path):
            kalshi_client = None
            print("  [WARN] Kalshi auth failed.")
        else:
            print("  Kalshi authenticated successfully.")
    else:
        print("\n  [SKIP] KALSHI_API_KEY_ID/KALSHI_PRIVATE_KEY_PATH not set in .env")

    pm_trader = None
    pm_private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    if pm_private_key and not dry_run:
        pm_chain_id = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
        try:
            pm_trader = PolymarketTrader(pm_private_key, pm_chain_id)
            print("  Polymarket trader initialized.")
        except Exception as e:
            print(f"  [WARN] Polymarket trader init failed: {e}")

    executor = ArbitrageExecutor(
        pm_trader=pm_trader,
        kalshi_client=kalshi_client,
        db=db,
        risk_manager=risk_manager,
        dry_run=dry_run,
        exec_mode=exec_mode,
        max_trade_size=max_trade,
    )

    if args.continuous:
        _run_continuous(args, min_profit, kalshi_client, kalshi_api_key_id,
                        kalshi_private_key_path, executor, db)
    else:
        _run_oneshot(args, min_profit, kalshi_client, executor, db)

    db.close()


def _run_oneshot(args, min_profit, kalshi_client, executor, db):
    """Original one-shot scan mode with optional execution."""
    all_opportunities = []
    poly_markets = None
    poly_events = None

    if args.mode in ("all", "binary", "negrisk", "cross"):
        print("\n[1/3] Fetching Polymarket markets...")
        poly_markets = fetch_all_markets()
        print(f"  Fetched {len(poly_markets)} active markets.")

    if args.mode in ("all", "negrisk"):
        print("\n[2/3] Fetching Polymarket events...")
        poly_events = fetch_events()
        print(f"  Fetched {len(poly_events)} active events.")

    # Scan binary internal
    if args.mode in ("all", "binary"):
        print("\n--- Binary Internal Scan ---")
        binary_opps = scan_binary_internal(poly_markets, min_profit)
        all_opportunities.extend(binary_opps)
        print(f"  Found {len(binary_opps)} opportunities.")

    # Scan NegRisk internal
    if args.mode in ("all", "negrisk"):
        print("\n--- NegRisk Internal Scan ---")
        negrisk_opps = scan_negrisk_internal(poly_events, min_profit)
        all_opportunities.extend(negrisk_opps)
        print(f"  Found {len(negrisk_opps)} opportunities.")

    # Scan cross-platform
    if args.mode in ("all", "cross"):
        print("\n--- Cross-Platform Scan (Polymarket vs Kalshi) ---")
        cross_opps = scan_cross_platform(
            poly_markets, kalshi_client, min_profit,
            min_confidence=args.min_confidence,
        )
        all_opportunities.extend(cross_opps)
        print(f"  Found {len(cross_opps)} opportunities.")

    # Filter by minimum depth if specified
    if args.min_depth > 0:
        before = len(all_opportunities)
        all_opportunities = [
            opp for opp in all_opportunities
            if opp.get("_clob_depth", 0) >= args.min_depth
        ]
        filtered = before - len(all_opportunities)
        if filtered:
            print(f"\n  Filtered out {filtered} opportunities below min depth {args.min_depth:.0f}")

    # Sort by net profit descending
    all_opportunities.sort(key=lambda x: x["net_profit"], reverse=True)

    if args.limit:
        all_opportunities = all_opportunities[:args.limit]

    # Display results
    _display_results(all_opportunities, args.json)

    # Execute opportunities if not display-only
    if all_opportunities and (executor.dry_run or executor.exec_mode in ("semi-auto", "full-auto")):
        print("\n--- Execution Pass ---")
        executed = 0
        for opp in all_opportunities:
            if executor.execute(opp):
                executed += 1
        print(f"\n  Executed: {executed}/{len(all_opportunities)}")


def _run_continuous(args, min_profit, kalshi_client, kalshi_api_key_id,
                    kalshi_private_key_path, executor, db):
    """Continuous mode: periodic re-scans with WebSocket price feeds."""
    RESCAN_INTERVAL = 300  # 5 minutes between full re-scans

    shutdown_event = asyncio.Event()

    def _signal_handler(sig, frame):
        print("\n\n  Shutting down gracefully...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Price cache updated by WebSocket feeds
    price_cache = {}

    def on_price_update(platform, ticker, data):
        price_cache[(platform, ticker)] = data

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
            print(f"\n{'='*80}")
            print(f"  CONTINUOUS SCAN #{scan_count}")
            print(f"{'='*80}")

            try:
                # Full market discovery scan
                poly_markets = fetch_all_markets()
                poly_events = fetch_events() if args.mode in ("all", "negrisk") else None

                all_opportunities = []

                if args.mode in ("all", "binary"):
                    binary_opps = scan_binary_internal(poly_markets, min_profit)
                    all_opportunities.extend(binary_opps)

                if args.mode in ("all", "negrisk") and poly_events:
                    negrisk_opps = scan_negrisk_internal(poly_events, min_profit)
                    all_opportunities.extend(negrisk_opps)

                if args.mode in ("all", "cross"):
                    cross_opps = scan_cross_platform(
                        poly_markets, kalshi_client, min_profit,
                        min_confidence=args.min_confidence,
                    )
                    all_opportunities.extend(cross_opps)

                # Apply filters
                if args.min_depth > 0:
                    all_opportunities = [
                        opp for opp in all_opportunities
                        if opp.get("_clob_depth", 0) >= args.min_depth
                    ]

                all_opportunities.sort(key=lambda x: x["net_profit"], reverse=True)

                if args.limit:
                    all_opportunities = all_opportunities[:args.limit]

                _display_results(all_opportunities, args.json)

                # Execute opportunities
                if all_opportunities:
                    print("\n--- Execution Pass ---")
                    executed = 0
                    for opp in all_opportunities:
                        if shutdown_event.is_set():
                            break
                        if executor.execute(opp):
                            executed += 1
                    print(f"  Executed: {executed}/{len(all_opportunities)}")

                # Subscribe to WebSocket feeds for discovered markets
                # (on first scan or when new markets appear)
                if scan_count == 1 and not ws_task:
                    # Collect token IDs from Polymarket markets for WS subscription
                    poly_token_ids = []
                    for m in (poly_markets or []):
                        token_ids_raw = m.get("clobTokenIds")
                        if token_ids_raw:
                            try:
                                ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw
                                poly_token_ids.extend(ids[:2])
                            except (json.JSONDecodeError, ValueError):
                                pass
                    # Limit WS subscriptions to keep it manageable
                    feed_manager.subscribe_polymarket(poly_token_ids[:100])

                    if kalshi_client:
                        kalshi_events = kalshi_client.fetch_all_events()
                        kalshi_tickers = [
                            e.get("event_ticker", "") for e in (kalshi_events or [])
                            if e.get("event_ticker")
                        ][:100]
                        feed_manager.subscribe_kalshi(kalshi_tickers)

                    ws_task = asyncio.create_task(feed_manager.run())

            except Exception as e:
                print(f"  [ERROR] Scan failed: {e}")

            # Wait for next scan interval or shutdown
            print(f"\n  Next scan in {RESCAN_INTERVAL}s (Ctrl+C to stop)...")
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=RESCAN_INTERVAL)
            except asyncio.TimeoutError:
                pass

        # Cleanup
        print("  Stopping WebSocket feeds...")
        feed_manager.stop()
        if ws_task:
            ws_task.cancel()
            try:
                await ws_task
            except (asyncio.CancelledError, Exception):
                pass
        print("  Shutdown complete.")

    asyncio.run(_continuous_loop())


def _display_results(all_opportunities: list[dict], json_output: bool = False):
    """Display scan results as table or JSON."""
    print("\n" + "=" * 80)
    print(f"  RESULTS: {len(all_opportunities)} arbitrage opportunities found")
    print("=" * 80 + "\n")

    if not all_opportunities:
        print("  No opportunities above the minimum profit threshold.")
        print("  Try lowering --min-profit or check back later.")
        return

    if json_output:
        output = []
        for opp in all_opportunities:
            entry = {
                "type": opp["type"],
                "market": opp["market"],
                "prices": opp["prices"],
                "total_cost": opp["total_cost"],
                "gross_spread": opp["gross_spread"],
                "fees": opp["fees"],
                "net_profit": f"${opp['net_profit']:.4f}",
                "net_roi": opp["net_roi"],
                "volume": opp.get("volume", ""),
            }
            if "kalshi" in opp:
                entry["kalshi_market"] = opp["kalshi"]
                entry["match_score"] = opp["match"]
                entry["confidence"] = opp.get("confidence", "")
            if "_clob_depth" in opp:
                entry["depth"] = opp["_clob_depth"]
            output.append(entry)
        print(json.dumps(output, indent=2))
    else:
        has_cross = any("kalshi" in opp for opp in all_opportunities)
        has_depth = any("_clob_depth" in opp for opp in all_opportunities)
        table_data = []
        for opp in all_opportunities:
            row = [
                opp["type"],
                opp["market"],
            ]
            if has_cross:
                row.append(opp.get("kalshi", ""))
                row.append(opp.get("match", ""))
                row.append(opp.get("confidence", ""))
            row.extend([
                opp["prices"],
                opp["total_cost"],
                f"${opp['net_profit']:.4f}",
                opp["net_roi"],
                opp.get("volume", ""),
            ])
            if has_depth:
                depth = opp.get("_clob_depth")
                row.append(f"{depth:.0f}" if depth is not None else "")
            table_data.append(row)

        headers = ["Type", "Polymarket"]
        if has_cross:
            headers.extend(["Kalshi", "Match", "Conf"])
        headers.extend(["Prices", "Cost", "Net Profit", "ROI", "Volume"])
        if has_depth:
            headers.append("Depth")
        print(tabulate(table_data, headers=headers, tablefmt="grid", maxcolwidths=50))

    print(f"\n  Disclaimer: Prices are snapshots. Verify on-chain before trading.")
    print(f"  Opportunities may close within milliseconds.\n")


if __name__ == "__main__":
    main()
