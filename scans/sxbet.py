"""SX Bet Exchange standalone arbitrage scans (back-all and back-lay)."""

import logging

from sxbet_api import SXBetClient
from fees import net_profit_sxbet_backall, net_profit_sxbet_backlay
from scans.helpers import filter_dust

logger = logging.getLogger(__name__)


def scan_sxbet_backall(sxbet_client: SXBetClient, min_profit: float) -> list[dict]:
    """Scan for SX Bet back-all arbitrage (under-round books).

    SX Bet markets are binary (outcomeOne / outcomeTwo). Back-all arb
    exists when buying YES on both outcomes costs < $1.00 total.
    SX Bet has 0% commission so full spread is profit.

    Uses batch orderbook fetching to avoid rate limits.
    """
    opportunities = []

    if not sxbet_client or not sxbet_client.authenticated:
        return opportunities

    markets = sxbet_client.fetch_all_markets()
    if not markets:
        logger.warning("No SX Bet markets fetched.")
        return opportunities

    logger.info("Scanning %d SX Bet markets for back-all arbs...", len(markets))

    # Batch fetch all orderbooks (20 per API call instead of 1)
    market_hashes = [m.get("marketHash", "") for m in markets if m.get("marketHash")]
    orderbooks = sxbet_client.get_orderbooks_batch(market_hashes, batch_size=20)

    for market in markets:
        market_hash = market.get("marketHash", "")
        if not market_hash:
            continue

        ob = orderbooks.get(market_hash)
        if not ob:
            continue

        bids = ob.get("bids", [])
        asks = ob.get("asks", [])

        # For binary back-all: need best YES price (from bids) and best NO price (from asks)
        # bids = YES side (isMakerBettingOutcomeOne=true), sorted highest first
        # asks = NO side (isMakerBettingOutcomeOne=false), sorted lowest first
        if not bids or not asks:
            continue

        yes_price = float(bids[0]["price"])
        no_price = float(asks[0]["price"])

        if yes_price <= 0 or no_price <= 0 or yes_price >= 1 or no_price >= 1:
            continue

        implied_probs = [yes_price, no_price]
        result = net_profit_sxbet_backall(implied_probs)

        if result["net_profit"] >= min_profit:
            total = sum(implied_probs)
            sport_info = market.get("_sport", {})
            sport_name = sport_info.get("label", market.get("sportLabel", ""))
            team1 = market.get("teamOneName", "")
            team2 = market.get("teamTwoName", "")
            league = market.get("leagueLabel", "")
            title = f"{team1} vs {team2}" if team1 and team2 else league or "Unknown"
            if sport_name:
                title = f"{sport_name} - {title}"

            bid_size = float(bids[0].get("size", 0))
            ask_size = float(asks[0].get("size", 0))

            opportunities.append({
                "type": "SXBetBackAll",
                "_layer": 1,
                "market": title[:60],
                "prices": f"{yes_price:.3f}, {no_price:.3f}",
                "total_cost": f"${total:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total * 100:.2f}%" if total > 0 else "0%",
                "_sx_market_hash": market_hash,
                "_sx_prices": implied_probs,
                "_clob_depth": min(bid_size, ask_size),
            })

    logger.info("Found %d SX Bet back-all opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities


def scan_sxbet_backlay(sxbet_client: SXBetClient, min_profit: float) -> list[dict]:
    """Scan for SX Bet back-lay arbitrage (crossed books on same outcome).

    Same outcome has best bid > best ask (crossed book).
    SX Bet has 0% commission so full spread is profit.

    Uses batch orderbook fetching to avoid rate limits.
    """
    opportunities = []

    if not sxbet_client or not sxbet_client.authenticated:
        return opportunities

    markets = sxbet_client.fetch_all_markets()
    if not markets:
        return opportunities

    logger.info("Scanning %d SX Bet markets for back-lay arbs...", len(markets))

    # Batch fetch all orderbooks
    market_hashes = [m.get("marketHash", "") for m in markets if m.get("marketHash")]
    orderbooks = sxbet_client.get_orderbooks_batch(market_hashes, batch_size=20)

    for market in markets:
        market_hash = market.get("marketHash", "")
        if not market_hash:
            continue

        ob = orderbooks.get(market_hash)
        if not ob:
            continue

        bids = ob.get("bids", [])
        asks = ob.get("asks", [])

        if not bids or not asks:
            continue

        best_bid = float(bids[0]["price"])
        best_ask = float(asks[0]["price"])

        if best_bid <= 0 or best_ask <= 0:
            continue

        # Crossed book: bid > ask means we can buy at ask and sell at bid
        if best_bid <= best_ask:
            continue

        back_prob = best_ask
        lay_prob = best_bid

        result = net_profit_sxbet_backlay(back_prob, lay_prob)
        if result["net_profit"] >= min_profit:
            sport_info = market.get("_sport", {})
            sport_name = sport_info.get("label", market.get("sportLabel", ""))
            team1 = market.get("teamOneName", "")
            team2 = market.get("teamTwoName", "")
            title = f"{team1} vs {team2}" if team1 and team2 else "Unknown"
            if sport_name:
                title = f"{sport_name} - {title}"

            bid_size = float(bids[0].get("size", 0))
            ask_size = float(asks[0].get("size", 0))

            opportunities.append({
                "type": "SXBetBackLay",
                "_layer": 1,
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
