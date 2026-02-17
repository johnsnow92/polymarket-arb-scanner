"""PredictIt standalone arbitrage scans (binary and multi-outcome)."""

import logging

from predictit_api import PredictItClient, MAX_POSITION_PER_CONTRACT
from fees import net_profit_predictit_binary, net_profit_predictit_multi
from scans.helpers import filter_dust

logger = logging.getLogger(__name__)


def _max_shares(price: float) -> int:
    """Calculate max shares allowed under CFTC $850 per-contract limit."""
    if price <= 0:
        return 0
    return int(MAX_POSITION_PER_CONTRACT / price)


def scan_predictit_binary(predictit_client: PredictItClient, min_profit: float) -> list[dict]:
    """Scan for PredictIt binary arbitrage (YES + NO < $1.00 on same contract)."""
    opportunities = []

    if not predictit_client:
        return opportunities

    markets = predictit_client.fetch_all_markets()
    if not markets:
        logger.warning("No PredictIt markets fetched.")
        return opportunities

    logger.info("Scanning %d PredictIt markets for binary arbs...", len(markets))

    for market in markets:
        contracts = market.get("contracts", [])
        if len(contracts) != 1:
            continue  # Binary = exactly 1 contract

        contract = contracts[0]
        yes_price = contract.get("bestBuyYesCost")
        no_price = contract.get("bestBuyNoCost")

        if yes_price is None or no_price is None:
            continue
        try:
            yes_price = float(yes_price)
            no_price = float(no_price)
        except (ValueError, TypeError):
            continue

        if yes_price <= 0.01 or no_price <= 0.01:
            continue

        result = net_profit_predictit_binary(yes_price, no_price)
        if result["net_profit"] >= min_profit:
            total_cost = yes_price + no_price
            contract_id = contract.get("id")
            opportunities.append({
                "type": "PIBinary",
                "market": market.get("name", market.get("shortName", "Unknown"))[:60],
                "prices": f"Y={yes_price:.3f} N={no_price:.3f}",
                "total_cost": f"${total_cost:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total_cost * 100:.2f}%",
                "_contract_id": contract_id,
                "_pi_yes": yes_price,
                "_pi_no": no_price,
                "_max_shares": _max_shares(min(yes_price, no_price)),
                "_clob_depth": _max_shares(min(yes_price, no_price)),
            })

    logger.info("Found %d PredictIt binary opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities


def scan_predictit_multi(predictit_client: PredictItClient, min_profit: float) -> list[dict]:
    """Scan for PredictIt multi-outcome arbitrage (sum of YES < $1.00 across contracts)."""
    opportunities = []

    if not predictit_client:
        return opportunities

    markets = predictit_client.fetch_all_markets()
    if not markets:
        return opportunities

    logger.info("Scanning %d PredictIt markets for multi-outcome arbs...", len(markets))

    for market in markets:
        contracts = market.get("contracts", [])
        if len(contracts) < 2:
            continue

        yes_prices = []
        contract_ids = []
        valid = True

        for contract in contracts:
            yes_price = contract.get("bestBuyYesCost")
            if yes_price is None:
                valid = False
                break
            try:
                yp = float(yes_price)
            except (ValueError, TypeError):
                valid = False
                break
            if yp <= 0:
                valid = False
                break
            yes_prices.append(yp)
            contract_ids.append(contract.get("id"))

        if not valid or not yes_prices:
            continue

        result = net_profit_predictit_multi(yes_prices)
        if result["net_profit"] >= min_profit:
            total = sum(yes_prices)
            n = len(yes_prices)
            price_summary = ", ".join(f"{p:.3f}" for p in sorted(yes_prices, reverse=True)[:5])
            if n > 5:
                price_summary += f"... ({n} total)"

            opportunities.append({
                "type": f"PIMulti({n})",
                "market": market.get("name", market.get("shortName", "Unknown"))[:60],
                "prices": price_summary,
                "total_cost": f"${total:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total * 100:.2f}%",
                "_pi_contract_ids": contract_ids,
                "_pi_prices": yes_prices,
                "_max_shares": min(_max_shares(p) for p in yes_prices),
                "_clob_depth": min(_max_shares(p) for p in yes_prices),
            })

    logger.info("Found %d PredictIt multi-outcome opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities
