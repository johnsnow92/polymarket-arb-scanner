"""Drift BET standalone arbitrage scans (binary only)."""

import logging

from drift_api import DriftClient
from fees import net_profit_drift_binary
from scans.helpers import filter_dust

logger = logging.getLogger(__name__)


def scan_drift_binary(drift_client: DriftClient, min_profit: float) -> list[dict]:
    """Scan for Drift BET binary arbitrage (YES + NO < $1.00 on same market)."""
    opportunities = []

    if not drift_client:
        return opportunities

    markets = drift_client.fetch_all_markets()
    if not markets:
        logger.warning("No Drift markets fetched.")
        return opportunities

    logger.info("Scanning %d Drift markets for binary arbs...", len(markets))

    for market in markets:
        yes_price, no_price = drift_client.get_market_price(market)

        if yes_price is None or no_price is None:
            continue
        if yes_price <= 0.01 or no_price <= 0.01:
            continue

        result = net_profit_drift_binary(yes_price, no_price)
        if result["net_profit"] >= min_profit:
            total_cost = yes_price + no_price
            market_id = market.get("id", market.get("marketId", market.get("publicKey", "")))
            opportunities.append({
                "type": "DriftBinary",
                "market": market.get("title", market.get("name", "Unknown"))[:60],
                "prices": f"Y={yes_price:.3f} N={no_price:.3f}",
                "total_cost": f"${total_cost:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total_cost * 100:.2f}%",
                "_drift_market_id": market_id,
                "_drift_yes": yes_price,
                "_drift_no": no_price,
                "_clob_depth": 1,
            })

    logger.info("Found %d Drift binary opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities
