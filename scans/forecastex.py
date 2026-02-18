"""ForecastEx (IBKR) binary arbitrage scan."""

import logging

from forecastex_api import ForecastExClient
from fees import net_profit_forecastex_binary
from scans.helpers import filter_dust

logger = logging.getLogger(__name__)


def scan_forecastex_binary(forecastex_client: ForecastExClient, min_profit: float) -> list[dict]:
    """Scan for ForecastEx binary arbitrage (YES + NO < $1.00).

    ForecastEx contracts cannot be sold, so arbitrage = buy YES + buy NO
    for less than $1 total. Both legs are BUY operations.
    """
    opportunities = []

    if not forecastex_client or not forecastex_client.authenticated:
        return opportunities

    markets = forecastex_client.fetch_all_markets()
    if not markets:
        logger.warning("No ForecastEx markets fetched.")
        return opportunities

    logger.info("Scanning %d ForecastEx markets for binary arbs...", len(markets))

    for market in markets:
        yes_price, no_price = forecastex_client.get_market_price(market)

        if yes_price is None or no_price is None:
            continue

        if yes_price <= 0.01 or no_price <= 0.01:
            continue
        if yes_price >= 0.99 or no_price >= 0.99:
            continue

        result = net_profit_forecastex_binary(yes_price, no_price)
        if result["net_profit"] >= min_profit:
            total_cost = yes_price + no_price
            market_id = market.get("id", market.get("contractId", market.get("conid", "")))
            market_name = market.get("title", market.get("name", market.get("shortName", "Unknown")))

            opportunities.append({
                "type": "ForecastExBinary",
                "market": str(market_name)[:60],
                "prices": f"Y={yes_price:.3f} N={no_price:.3f}",
                "total_cost": f"${total_cost:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total_cost * 100:.2f}%",
                "_fx_market_id": str(market_id),
                "_fx_yes": yes_price,
                "_fx_no": no_price,
                "_fx_buy_only": True,  # Reminder: can't sell, both legs are BUY
                "_clob_depth": float(market.get("volume", market.get("openInterest", 0)) or 0),
            })

    logger.info("Found %d ForecastEx binary opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities
