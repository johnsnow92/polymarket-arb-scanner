"""IBKR ForecastEx standalone arbitrage scan (binary internal only).

IBKR only supports BUY orders — no back-lay or sell. Only internal binary
arbs (BUY YES + BUY NO) are possible.
"""

import logging

from ibkr_api import IBKRClient
from fees import net_profit_ibkr_binary
from scans.helpers import filter_dust

logger = logging.getLogger(__name__)


def scan_ibkr_binary(ibkr_client: IBKRClient, min_profit: float) -> list[dict]:
    """Scan for IBKR ForecastEx binary arbitrage (under-round YES+NO).

    BUY YES + BUY NO. One always pays $1, other $0.
    IBKR has $0.00 commission. Both sides are BUY orders.
    """
    opportunities = []

    if not ibkr_client or not ibkr_client.authenticated:
        return opportunities

    events = ibkr_client.fetch_all_markets()
    if not events:
        logger.warning("No IBKR ForecastEx markets fetched.")
        return opportunities

    logger.info("Scanning %d IBKR ForecastEx events for binary arbs...", len(events))

    for event in events:
        contracts = event.get("contracts", [])
        if len(contracts) != 2:
            continue

        yes_price, no_price = ibkr_client.get_market_price(event)
        if yes_price is None or no_price is None:
            continue
        if yes_price <= 0 or no_price <= 0:
            continue

        result = net_profit_ibkr_binary(yes_price, no_price)
        if result["net_profit"] >= min_profit:
            total = yes_price + no_price

            # Extract conids for YES and NO
            yes_conid = ""
            no_conid = ""
            for c in contracts:
                side = (c.get("side") or c.get("label") or "").upper()
                if "YES" in side:
                    yes_conid = c.get("conid", "")
                elif "NO" in side:
                    no_conid = c.get("conid", "")
            # Fallback: assign by position
            if not yes_conid and len(contracts) >= 2:
                yes_conid = contracts[0].get("conid", "")
                no_conid = contracts[1].get("conid", "")

            opportunities.append({
                "type": "IBKRBinary",
                "_layer": 1,  # Layer 1: pure arbitrage
                "market": event.get("title", "")[:60],
                "prices": f"Y={yes_price:.3f} N={no_price:.3f}",
                "total_cost": f"${total:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total * 100:.2f}%",
                "_ibkr_event_id": event.get("id", ""),
                "_ibkr_yes_conid": yes_conid,
                "_ibkr_no_conid": no_conid,
                "_ibkr_yes_price": yes_price,
                "_ibkr_no_price": no_price,
                "_clob_depth": 0,
            })

    logger.info("Found %d IBKR binary opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities
