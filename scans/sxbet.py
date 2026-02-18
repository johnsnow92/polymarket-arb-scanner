"""SX Bet Exchange standalone arbitrage scans (back-all and back-lay)."""

import logging

from sxbet_api import SXBetClient
from fees import net_profit_sxbet_backall, net_profit_sxbet_backlay
from scans.helpers import filter_dust

logger = logging.getLogger(__name__)


def scan_sxbet_backall(sxbet_client: SXBetClient, min_profit: float) -> list[dict]:
    """Scan for SX Bet back-all arbitrage (under-round books).

    Sum of implied prices across all outcomes < 1.0.
    SX Bet has 0% commission so full spread is profit.
    """
    opportunities = []

    if not sxbet_client or not sxbet_client.authenticated:
        return opportunities

    markets = sxbet_client.fetch_all_markets()
    if not markets:
        logger.warning("No SX Bet markets fetched.")
        return opportunities

    logger.info("Scanning %d SX Bet markets for back-all arbs...", len(markets))

    for market in markets:
        market_hash = market.get("marketHash", "")
        if not market_hash:
            continue

        # Fetch outcomes and orderbook
        orderbook = sxbet_client.get_orderbook(market_hash)
        if not orderbook:
            continue

        # SX Bet markets may have multiple outcomes with separate orderbooks
        # For single-market scan, use the top-level bids as implied prices
        outcomes = market.get("outcomes", [])
        if len(outcomes) < 2:
            continue

        implied_probs = []
        outcome_ids = []
        valid = True

        for outcome in outcomes:
            outcome_id = outcome.get("outcomeId", "")
            # Fetch per-outcome orderbook
            ob = sxbet_client.get_orderbook(f"{market_hash}/{outcome_id}") if outcome_id else None

            # Fall back to using the outcome's last price or implied probability
            price = outcome.get("price") or outcome.get("impliedProbability")
            if ob and ob.get("bids"):
                price = float(ob["bids"][0].get("price", 0))

            if not price or float(price) <= 0 or float(price) >= 1:
                valid = False
                break

            implied_probs.append(float(price))
            outcome_ids.append(outcome_id)

        if not valid or not implied_probs:
            continue

        result = net_profit_sxbet_backall(implied_probs)
        if result["net_profit"] >= min_profit:
            total = sum(implied_probs)
            n = len(implied_probs)
            sport_info = market.get("_sport", {})
            sport_name = sport_info.get("label", "")
            market_title = market.get("title", market.get("label", "Unknown"))
            title = f"{sport_name} - {market_title}" if sport_name else market_title

            price_summary = ", ".join(
                f"{p:.3f}" for p in sorted(implied_probs, reverse=True)[:5]
            )
            if n > 5:
                price_summary += f"... ({n} outcomes)"

            # Depth from orderbook
            min_depth = 0
            if orderbook.get("bids"):
                for bid in orderbook["bids"][:1]:
                    size = float(bid.get("size", bid.get("amount", 0)))
                    min_depth = size if min_depth == 0 else min(min_depth, size)

            opportunities.append({
                "type": "SXBetBackAll",
                "market": title[:60],
                "prices": price_summary,
                "total_cost": f"${total:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total * 100:.2f}%",
                "_sx_market_hash": market_hash,
                "_sx_outcome_ids": outcome_ids,
                "_sx_prices": implied_probs,
                "_clob_depth": min_depth,
            })

    logger.info("Found %d SX Bet back-all opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities


def scan_sxbet_backlay(sxbet_client: SXBetClient, min_profit: float) -> list[dict]:
    """Scan for SX Bet back-lay arbitrage (crossed books on same outcome).

    Same outcome has best bid > best ask (crossed book).
    SX Bet has 0% commission so full spread is profit.
    """
    opportunities = []

    if not sxbet_client or not sxbet_client.authenticated:
        return opportunities

    markets = sxbet_client.fetch_all_markets()
    if not markets:
        return opportunities

    logger.info("Scanning %d SX Bet markets for back-lay arbs...", len(markets))

    for market in markets:
        market_hash = market.get("marketHash", "")
        if not market_hash:
            continue

        orderbook = sxbet_client.get_orderbook(market_hash)
        if not orderbook:
            continue

        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        if not bids or not asks:
            continue

        best_bid = float(bids[0].get("price", 0))
        best_ask = float(asks[0].get("price", 0))

        if best_bid <= 0 or best_ask <= 0:
            continue

        # Crossed book: bid > ask means we can buy at ask and sell at bid
        if best_bid <= best_ask:
            continue

        # In probability terms: back_prob = ask, lay_prob = bid
        back_prob = best_ask
        lay_prob = best_bid

        result = net_profit_sxbet_backlay(back_prob, lay_prob)
        if result["net_profit"] >= min_profit:
            sport_info = market.get("_sport", {})
            sport_name = sport_info.get("label", "")
            market_title = market.get("title", market.get("label", "Unknown"))
            title = f"{sport_name} - {market_title}" if sport_name else market_title

            bid_size = float(bids[0].get("size", bids[0].get("amount", 0)))
            ask_size = float(asks[0].get("size", asks[0].get("amount", 0)))

            opportunities.append({
                "type": "SXBetBackLay",
                "market": title[:60],
                "prices": f"back={back_prob:.3f} lay={lay_prob:.3f}",
                "total_cost": f"${back_prob:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": (f"{result['net_profit'] / back_prob * 100:.2f}%"
                            if back_prob > 0 else "0%"),
                "_sx_market_hash": market_hash,
                "_sx_back_price": back_prob,
                "_sx_lay_price": lay_prob,
                "_clob_depth": min(bid_size, ask_size),
            })

    logger.info("Found %d SX Bet back-lay opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities
