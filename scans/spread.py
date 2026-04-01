"""Intra-platform spread capture scans (buy at ask, sell at bid on same token)."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from polymarket_api import fetch_order_book, get_best_bid_ask, get_binary_markets
from fees import net_profit_spread_polymarket
from scans.helpers import _within_resolution_window, filter_dust, _days_to_resolution

logger = logging.getLogger(__name__)


def scan_spread_polymarket(markets: list[dict], min_profit: float) -> list[dict]:
    """Scan for spread capture opportunities on Polymarket.

    Finds tokens where bid > ask (crossed book), meaning you can buy and
    immediately sell for a profit (minus gas).
    """
    opportunities = []

    if not markets:
        return opportunities

    binary_markets = get_binary_markets(markets)
    logger.info("Scanning %d binary markets for Polymarket spreads...", len(binary_markets))

    # Extract all token IDs for parallel fetching
    token_tasks = []
    for m in binary_markets:
        if not _within_resolution_window(m, platform="polymarket"):
            continue
        token_ids_raw = m.get("clobTokenIds")
        if not token_ids_raw:
            continue
        import json
        try:
            if isinstance(token_ids_raw, str):
                token_ids = json.loads(token_ids_raw)
            else:
                token_ids = list(token_ids_raw)
        except (json.JSONDecodeError, ValueError):
            continue
        for tid in token_ids:
            token_tasks.append((m, tid))

    if not token_tasks:
        return opportunities

    # Fetch order books in parallel
    def _check_spread(market_token):
        market, token_id = market_token
        book = fetch_order_book(token_id)
        if not book:
            return None
        ba = get_best_bid_ask(book)
        bid = ba.get("bid")
        ask = ba.get("ask")
        if bid is None or ask is None or bid <= ask:
            return None

        result = net_profit_spread_polymarket(ask, bid)
        if result["net_profit"] >= min_profit:
            min_size = min(ba.get("bid_size", 0) or 0, ba.get("ask_size", 0) or 0)
            return {
                "type": "SpreadPM",
                "_layer": 1,  # Layer 1: pure arbitrage
                "market": market.get("question", market.get("title", "Unknown"))[:60],
                "prices": f"ask={ask:.3f} bid={bid:.3f}",
                "total_cost": f"${ask:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / ask * 100:.2f}%" if ask > 0 else "0%",
                "_token_id": token_id,
                "_ask_price": ask,
                "_bid_price": bid,
                "_spread_platform": "polymarket",
                "_clob_depth": min_size,
                "_days_to_resolution": _days_to_resolution(market, "polymarket"),
            }
        return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_check_spread, task): task for task in token_tasks}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    opportunities.append(result)
            except Exception as e:
                logger.debug("Spread check failed: %s", e)

    logger.info("Found %d Polymarket spread opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities
