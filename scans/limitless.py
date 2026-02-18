"""Limitless Exchange standalone arbitrage scans (binary only)."""

import logging
from datetime import datetime, timezone

from limitless_api import LimitlessClient
from fees import net_profit_limitless_binary
from scans.helpers import filter_dust

logger = logging.getLogger(__name__)


def _days_to_resolution_limitless(market: dict) -> float:
    """Calculate days until market resolution from Limitless market data.

    Returns a default of 7.0 days if no resolution date is available.
    """
    date_str = (
        market.get("resolutionDate")
        or market.get("resolution_date")
        or market.get("endDate")
        or market.get("end_date")
        or market.get("endDateIso")
    )

    if not date_str:
        return 7.0  # Default assumption

    try:
        if date_str.endswith("Z"):
            date_str = date_str[:-1] + "+00:00"
        resolve_dt = datetime.fromisoformat(date_str)
        if resolve_dt.tzinfo is None:
            resolve_dt = resolve_dt.replace(tzinfo=timezone.utc)
        days = (resolve_dt - datetime.now(timezone.utc)).total_seconds() / 86400
        return max(days, 0.01)  # Floor at 0.01 to avoid division by zero
    except (ValueError, TypeError):
        return 7.0


def scan_limitless_binary(limitless_client: LimitlessClient, min_profit: float) -> list[dict]:
    """Scan for Limitless binary arbitrage (YES + NO < $1.00 on same market)."""
    opportunities = []

    if not limitless_client:
        return opportunities

    markets = limitless_client.fetch_all_markets()
    if not markets:
        logger.warning("No Limitless markets fetched.")
        return opportunities

    logger.info("Scanning %d Limitless markets for binary arbs...", len(markets))

    for market in markets:
        yes_price, no_price = limitless_client.get_market_price(market)

        if yes_price is None or no_price is None:
            continue
        if yes_price <= 0.01 or no_price <= 0.01:
            continue

        days = _days_to_resolution_limitless(market)

        result = net_profit_limitless_binary(yes_price, no_price, days_to_resolution=days)
        if result["net_profit"] >= min_profit:
            total_cost = yes_price + no_price
            market_id = market.get("id", market.get("marketId", ""))
            opportunities.append({
                "type": "LimitlessBinary",
                "market": market.get("title", market.get("name", "Unknown"))[:60],
                "prices": f"Y={yes_price:.3f} N={no_price:.3f}",
                "total_cost": f"${total_cost:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total_cost * 100:.2f}%",
                "_limitless_market_id": market_id,
                "_limitless_yes": yes_price,
                "_limitless_no": no_price,
                "_days_to_resolution": days,
                "_clob_depth": 1,
            })

    logger.info("Found %d Limitless binary opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities
