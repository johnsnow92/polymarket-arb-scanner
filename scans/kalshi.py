"""Kalshi-specific arbitrage scans (binary and multi-outcome)."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from kalshi_api import KalshiClient
from fees import net_profit_kalshi_binary, net_profit_kalshi_multi
from scans.helpers import _parallel_fetch_kalshi

logger = logging.getLogger(__name__)


def _fetch_kalshi_data(kalshi_client: KalshiClient) -> tuple[list[dict], dict, dict]:
    """Shared fetch: get all Kalshi events and their markets once.

    Returns (events, markets_by_event, event_titles).
    """
    if not kalshi_client:
        return [], {}, {}

    logger.info("Fetching all Kalshi events...")
    events = kalshi_client.fetch_all_events()
    if not events:
        logger.warning("No Kalshi events fetched.")
        return [], {}, {}

    logger.info("Fetched %d Kalshi events.", len(events))
    tickers = [e.get("event_ticker", "") for e in events if e.get("event_ticker")]
    markets_by_event = _parallel_fetch_kalshi(kalshi_client, tickers)
    event_titles = {e.get("event_ticker", ""): e.get("title", "Unknown") for e in events}
    return events, markets_by_event, event_titles


def scan_kalshi_binary(
    kalshi_client: KalshiClient,
    min_profit: float,
    kalshi_data: tuple | None = None,
) -> list[dict]:
    """Scan for Kalshi binary arbitrage (YES + NO < $1.00 on same market)."""
    opportunities = []

    if not kalshi_client:
        logger.info("Kalshi credentials not configured.")
        return opportunities

    if kalshi_data:
        events, markets_by_event, _ = kalshi_data
    else:
        events, markets_by_event, _ = _fetch_kalshi_data(kalshi_client)

    if not markets_by_event:
        return opportunities

    total_markets = 0
    for event_ticker, markets in markets_by_event.items():
        for km in markets:
            total_markets += 1
            yes_price, no_price = kalshi_client.get_market_price(km)
            if yes_price is None or no_price is None:
                continue
            if yes_price <= 0.001 or no_price <= 0.001:
                continue

            result = net_profit_kalshi_binary(yes_price, no_price)
            if result["net_profit"] >= min_profit:
                ticker = km.get("ticker", "")
                total_cost = yes_price + no_price
                opportunities.append({
                    "type": "KalshiBinary",
                    "market": km.get("title", "")[:60],
                    "prices": f"Y={yes_price:.3f} N={no_price:.3f}",
                    "total_cost": f"${total_cost:.4f}",
                    "gross_spread": f"{result['gross_spread']:.4f}",
                    "fees": f"${result['fees']:.4f}",
                    "net_profit": result["net_profit"],
                    "net_roi": f"{result['net_profit'] / total_cost * 100:.2f}%",
                    "_kalshi_ticker": ticker,
                    "_kalshi_yes": yes_price,
                    "_kalshi_no": no_price,
                })

    logger.info("Scanned %d Kalshi markets across %d events.", total_markets, len(events))

    # Stage 2: Re-fetch order book depth for top candidates (parallel)
    if opportunities:
        logger.info("Fetching order book depth for %d candidates...", len(opportunities))

        def _fetch_depth(opp):
            ticker = opp.get("_kalshi_ticker", "")
            if not ticker:
                return opp, 0
            depth = kalshi_client.get_order_book_depth(ticker)
            if depth:
                return opp, min(depth.get("yes_ask_size", 0), depth.get("no_ask_size", 0))
            return opp, 0

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_depth, opp): opp for opp in opportunities}
            for future in as_completed(futures):
                opp, d = future.result()
                opp["_clob_depth"] = d

    return opportunities


def scan_kalshi_multi(
    kalshi_client: KalshiClient,
    min_profit: float,
    kalshi_data: tuple | None = None,
) -> list[dict]:
    """Scan for Kalshi multi-outcome arbitrage (sum of YES prices < $1.00 across event)."""
    opportunities = []

    if not kalshi_client:
        logger.info("Kalshi credentials not configured.")
        return opportunities

    if kalshi_data:
        _, markets_by_event, event_titles = kalshi_data
    else:
        _, markets_by_event, event_titles = _fetch_kalshi_data(kalshi_client)

    if not markets_by_event:
        return opportunities

    for event_ticker, markets in markets_by_event.items():
        if len(markets) < 2:
            continue

        yes_prices = []
        market_tickers = []
        valid = True

        for km in markets:
            yes_price, _ = kalshi_client.get_market_price(km)
            if yes_price is None or yes_price <= 0:
                valid = False
                break
            yes_prices.append(yes_price)
            market_tickers.append(km.get("ticker", ""))

        if not valid or not yes_prices:
            continue

        # Sanity check: very low total with many outcomes likely means missing markets
        total_yes = sum(yes_prices)
        if len(yes_prices) >= 3 and total_yes < 0.50:
            event_title = event_titles.get(event_ticker, "Unknown")[:60]
            logger.warning("Likely missing outcomes: '%s' (%d outcomes sum to %.3f)",
                          event_title, len(yes_prices), total_yes)
            continue

        result = net_profit_kalshi_multi(yes_prices)
        if result["net_profit"] >= min_profit:
            total = sum(yes_prices)
            n = len(yes_prices)
            price_summary = ", ".join(f"{p:.3f}" for p in sorted(yes_prices, reverse=True)[:5])
            if n > 5:
                price_summary += f"... ({n} total)"

            event_title = event_titles.get(event_ticker, "Unknown")
            opportunities.append({
                "type": f"KalshiMulti({n})",
                "market": event_title[:60],
                "prices": price_summary,
                "total_cost": f"${total:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total * 100:.2f}%",
                "_kalshi_tickers": market_tickers,
                "_kalshi_prices": yes_prices,
            })

    # Stage 2: Re-fetch order book depth for candidates (parallel, min depth across all legs)
    if opportunities:
        logger.info("Fetching order book depth for %d multi-outcome candidates...", len(opportunities))

        def _fetch_multi_depth(opp):
            min_d = float("inf")
            for ticker in opp.get("_kalshi_tickers", []):
                if ticker:
                    depth = kalshi_client.get_order_book_depth(ticker)
                    if depth:
                        d = depth.get("yes_ask_size", 0)
                        min_d = min(min_d, d)
                    else:
                        min_d = 0
                        break
            return opp, min_d if min_d != float("inf") else 0

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_multi_depth, opp): opp for opp in opportunities}
            for future in as_completed(futures):
                opp, d = future.result()
                opp["_clob_depth"] = d

    return opportunities
