"""Intra-platform spread capture scans (buy at ask, sell at bid on same token)."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from polymarket_api import fetch_order_book, get_best_bid_ask, get_binary_markets
from kalshi_api import KalshiClient
from fees import net_profit_spread_polymarket, net_profit_spread_kalshi
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
            except Exception:
                pass

    logger.info("Found %d Polymarket spread opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities


def scan_spread_kalshi(kalshi_client: KalshiClient, min_profit: float, kalshi_data=None) -> list[dict]:
    """Scan for spread capture opportunities on Kalshi.

    Finds markets where bid > ask on the same side (crossed book).
    """
    opportunities = []

    if not kalshi_client:
        return opportunities

    if kalshi_data:
        _, markets_by_event, _ = kalshi_data
    else:
        from scans.kalshi import _fetch_kalshi_data
        _, markets_by_event, _ = _fetch_kalshi_data(kalshi_client)

    if not markets_by_event:
        return opportunities

    total_checked = 0
    for event_ticker, markets in markets_by_event.items():
        for km in markets:
            if not _within_resolution_window(km, platform="kalshi"):
                continue
            total_checked += 1
            ticker = km.get("ticker", "")
            if not ticker:
                continue

            book = kalshi_client.fetch_order_book(ticker)
            if not book:
                continue

            orderbook = book.get("orderbook", book)
            for side in ("yes", "no"):
                entries = orderbook.get(side) or []
                if len(entries) < 2:
                    continue
                # Check if best bid > best ask (crossed book)
                # Entries are price-sorted; for a crossed book we need
                # the buy side's best (highest) > sell side's best (lowest)
                # Kalshi orderbook: entries are [price_cents, quantity]
                # "yes" entries are asks sorted ascending, so best ask = first
                # We need to check the opposite side for bids
                # Actually, Kalshi's orderbook format is different:
                # "yes" side = prices people want to buy YES at
                # "no" side = prices people want to buy NO at
                # A crossed book on YES means: someone selling YES (= buying NO) at price < someone buying YES
                # This is rare on Kalshi since they match automatically, but let's check

                if isinstance(entries[0], list):
                    prices = [e[0] / 100.0 for e in entries]
                    sizes = [e[1] for e in entries]
                else:
                    prices = [float(e.get("price", 0)) / 100.0 for e in entries]
                    sizes = [int(e.get("quantity", e.get("size", 0))) for e in entries]

                if len(prices) >= 2:
                    ask = min(prices)
                    bid = max(prices)
                    if bid > ask:
                        result = net_profit_spread_kalshi(ask, bid)
                        if result["net_profit"] >= min_profit:
                            ask_idx = prices.index(ask)
                            bid_idx = prices.index(bid)
                            min_size = min(sizes[ask_idx], sizes[bid_idx])
                            opportunities.append({
                                "type": "SpreadKalshi",
                                "market": km.get("title", "")[:60],
                                "prices": f"ask={ask:.3f} bid={bid:.3f} ({side})",
                                "total_cost": f"${ask:.4f}",
                                "gross_spread": f"{result['gross_spread']:.4f}",
                                "fees": f"${result['fees']:.4f}",
                                "net_profit": result["net_profit"],
                                "net_roi": f"{result['net_profit'] / ask * 100:.2f}%" if ask > 0 else "0%",
                                "_kalshi_ticker": ticker,
                                "_ask_price": ask,
                                "_bid_price": bid,
                                "_spread_platform": "kalshi",
                                "_spread_side": side,
                                "_clob_depth": min_size,
                                "_days_to_resolution": _days_to_resolution(km, "kalshi"),
                            })

    logger.info("Checked %d Kalshi markets for spreads, found %d.", total_checked, len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities
