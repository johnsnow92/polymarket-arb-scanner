"""Opinion Exchange standalone arbitrage scans (binary and multi-outcome)."""

import logging

from opinion_api import OpinionClient
from fees import net_profit_opinion_binary, net_profit_opinion_multi
from scans.helpers import filter_dust

logger = logging.getLogger(__name__)


def scan_opinion_binary(opinion_client: OpinionClient, min_profit: float) -> list[dict]:
    """Scan for Opinion binary arbitrage (YES + NO < $1.00 on same market)."""
    opportunities = []

    if not opinion_client:
        return opportunities

    markets = opinion_client.fetch_all_markets()
    if not markets:
        logger.warning("No Opinion markets fetched.")
        return opportunities

    logger.info("Scanning %d Opinion markets for binary arbs...", len(markets))

    for market in markets:
        # Opinion binary markets have exactly 2 outcomes
        outcomes = market.get("outcomes", [])
        if len(outcomes) != 2:
            continue

        yes_price, no_price = opinion_client.get_market_price(market)

        if yes_price is None or no_price is None:
            continue
        if yes_price <= 0.01 or no_price <= 0.01:
            continue

        result = net_profit_opinion_binary(yes_price, no_price)
        if result["net_profit"] >= min_profit:
            total_cost = yes_price + no_price
            market_id = market.get("id", market.get("marketId", ""))
            opportunities.append({
                "type": "OpinionBinary",
                "market": market.get("title", market.get("name", "Unknown"))[:60],
                "prices": f"Y={yes_price:.3f} N={no_price:.3f}",
                "total_cost": f"${total_cost:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total_cost * 100:.2f}%",
                "_opinion_market_id": market_id,
                "_opinion_yes": yes_price,
                "_opinion_no": no_price,
                "_clob_depth": 1,
            })

    logger.info("Found %d Opinion binary opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities


def scan_opinion_multi(opinion_client: OpinionClient, min_profit: float) -> list[dict]:
    """Scan for Opinion multi-outcome arbitrage (sum of YES < $1.00 across outcomes)."""
    opportunities = []

    if not opinion_client:
        return opportunities

    markets = opinion_client.fetch_all_markets()
    if not markets:
        return opportunities

    logger.info("Scanning %d Opinion markets for multi-outcome arbs...", len(markets))

    for market in markets:
        outcomes = market.get("outcomes", [])
        if len(outcomes) < 3:
            continue  # Multi = 3+ outcomes

        yes_prices = []
        valid = True

        for outcome in outcomes:
            price = outcome.get("yesPrice", outcome.get("yes_price", outcome.get("price")))
            if price is None:
                valid = False
                break
            try:
                yp = float(price)
            except (ValueError, TypeError):
                valid = False
                break
            if yp <= 0:
                valid = False
                break
            yes_prices.append(yp)

        if not valid or not yes_prices:
            continue

        result = net_profit_opinion_multi(yes_prices)
        if result["net_profit"] >= min_profit:
            total = sum(yes_prices)
            n = len(yes_prices)
            price_summary = ", ".join(f"{p:.3f}" for p in sorted(yes_prices, reverse=True)[:5])
            if n > 5:
                price_summary += f"... ({n} total)"

            market_id = market.get("id", market.get("marketId", ""))
            opportunities.append({
                "type": f"OpinionMulti({n})",
                "market": market.get("title", market.get("name", "Unknown"))[:60],
                "prices": price_summary,
                "total_cost": f"${total:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total * 100:.2f}%",
                "_opinion_market_id": market_id,
                "_opinion_prices": yes_prices,
                "_clob_depth": 1,
            })

    logger.info("Found %d Opinion multi-outcome opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities
