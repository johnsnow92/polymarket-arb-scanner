"""Gemini Predictions standalone arbitrage scans (binary and multi-outcome)."""

import logging

from gemini_api import GeminiClient
from fees import net_profit_gemini_binary, net_profit_gemini_multi
from config import GEMINI_FEE_RATE
from scans.helpers import filter_dust

logger = logging.getLogger(__name__)


def scan_gemini_binary(gemini_client: GeminiClient, min_profit: float) -> list[dict]:
    """Scan for Gemini binary arbitrage (under-round YES+NO).

    For each binary event: if YES + NO < 1.0, guaranteed profit.
    Gemini fee: min(P, 1-P) * fee_rate per contract (5% taker, 1% maker).
    """
    opportunities = []

    if not gemini_client or not gemini_client.authenticated:
        return opportunities

    events = gemini_client.fetch_all_markets()
    if not events:
        logger.warning("No Gemini markets fetched.")
        return opportunities

    binary_events = [e for e in events if e.get("type") == "binary"]
    logger.info("Scanning %d Gemini binary events for arbs...", len(binary_events))

    for event in binary_events:
        yes_price, no_price = gemini_client.get_market_price(event)
        if yes_price is None or no_price is None:
            continue
        if yes_price <= 0 or no_price <= 0:
            continue

        result = net_profit_gemini_binary(yes_price, no_price, fee_rate=GEMINI_FEE_RATE)
        if result["net_profit"] >= min_profit:
            total = yes_price + no_price
            contracts = event.get("contracts", [])
            yes_symbol = ""
            no_symbol = ""
            for c in contracts:
                label = (c.get("label") or c.get("outcome") or "").lower()
                if "yes" in label:
                    yes_symbol = c.get("instrumentSymbol", "")
                elif "no" in label:
                    no_symbol = c.get("instrumentSymbol", "")
            # Fallback: assign by position
            if not yes_symbol and len(contracts) >= 2:
                yes_symbol = contracts[0].get("instrumentSymbol", "")
                no_symbol = contracts[1].get("instrumentSymbol", "")

            # Depth = min ask-side liquidity across YES and NO books,
            # since the arb requires buying one contract of each.
            depth = 0
            if yes_symbol and no_symbol:
                yes_book = gemini_client.get_order_book(yes_symbol, limit=1)
                no_book = gemini_client.get_order_book(no_symbol, limit=1)
                yes_depth = 0
                no_depth = 0
                if yes_book and yes_book.get("asks"):
                    yes_depth = yes_book["asks"][0].get("amount", 0)
                if no_book and no_book.get("asks"):
                    no_depth = no_book["asks"][0].get("amount", 0)
                if yes_depth and no_depth:
                    depth = min(yes_depth, no_depth)

            opportunities.append({
                "type": "GeminiBinary",
                "market": event.get("title", "")[:60],
                "prices": f"Y={yes_price:.3f} N={no_price:.3f}",
                "total_cost": f"${total:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total * 100:.2f}%",
                "_gm_event_id": event.get("id", ""),
                "_gm_yes_symbol": yes_symbol,
                "_gm_no_symbol": no_symbol,
                "_gm_yes_price": yes_price,
                "_gm_no_price": no_price,
                "_clob_depth": depth,
            })

    logger.info("Found %d Gemini binary opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities


def scan_gemini_multi(gemini_client: GeminiClient, min_profit: float) -> list[dict]:
    """Scan for Gemini categorical (multi-outcome) arbitrage.

    For each categorical event: if sum of all YES prices < 1.0, guaranteed profit.
    """
    opportunities = []

    if not gemini_client or not gemini_client.authenticated:
        return opportunities

    events = gemini_client.fetch_all_markets()
    if not events:
        return opportunities

    categorical_events = [e for e in events if e.get("type") == "categorical"]
    logger.info("Scanning %d Gemini categorical events for arbs...", len(categorical_events))

    for event in categorical_events:
        contracts = event.get("contracts", [])
        if len(contracts) < 3:
            continue

        prices = []
        symbols = []
        valid = True

        for c in contracts:
            price = c.get("price")
            if price is None or float(price) <= 0:
                valid = False
                break
            prices.append(float(price))
            symbols.append(c.get("instrumentSymbol", ""))

        if not valid or not prices:
            continue

        result = net_profit_gemini_multi(prices, fee_rate=GEMINI_FEE_RATE)
        if result["net_profit"] >= min_profit:
            total = sum(prices)
            n = len(prices)
            price_summary = ", ".join(f"{p:.3f}" for p in sorted(prices, reverse=True)[:5])
            if n > 5:
                price_summary += f"... ({n} outcomes)"

            # Fetch order book depth for each outcome — the bottleneck
            # is the thinnest book since the arb requires one contract
            # of every outcome.
            min_depth = float("inf")
            for sym in symbols:
                if not sym:
                    min_depth = 0
                    break
                book = gemini_client.get_order_book(sym, limit=1)
                if book and book.get("asks"):
                    amt = book["asks"][0].get("amount", 0)
                    min_depth = min(min_depth, amt)
                else:
                    min_depth = 0
                    break
            if min_depth == float("inf"):
                min_depth = 0

            opportunities.append({
                "type": "GeminiMulti",
                "market": event.get("title", "")[:60],
                "prices": price_summary,
                "total_cost": f"${total:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total * 100:.2f}%",
                "_gm_event_id": event.get("id", ""),
                "_gm_symbols": symbols,
                "_gm_prices": prices,
                "_clob_depth": min_depth,
            })

    logger.info("Found %d Gemini multi-outcome opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities
