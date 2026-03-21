"""Betfair Exchange standalone arbitrage scans (back-all and back-lay)."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from betfair_api import BetfairClient
from fees import net_profit_betfair_backall, net_profit_betfair_backlay
import config
from scans.helpers import filter_dust

logger = logging.getLogger(__name__)


def scan_betfair_backall(betfair_client: BetfairClient, min_profit: float) -> list[dict]:
    """Scan for Betfair back-all arbitrage (under-round books).

    Sum of implied back prices across all runners < 1.0.
    """
    opportunities = []

    if not betfair_client or not betfair_client.authenticated:
        return opportunities

    # Fetch events (politics category for prediction market relevance)
    events = betfair_client.list_events()
    if not events:
        logger.warning("No Betfair events fetched.")
        return opportunities

    logger.info("Scanning %d Betfair events for back-all arbs...", len(events))

    # Fetch market catalogues for each event in parallel
    def _fetch_event_markets(event):
        ev_data = event.get("event", {})
        ev_id = ev_data.get("id", "")
        if not ev_id:
            return []
        return betfair_client.list_markets(ev_id)

    all_markets = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_fetch_event_markets, e): e for e in events[:100]}
        for future in as_completed(futures):
            try:
                markets = future.result()
                all_markets.extend(markets)
            except Exception as e:
                logger.debug("Betfair market fetch failed: %s", e)

    logger.info("Fetched %d Betfair market catalogues.", len(all_markets))

    # Fetch market books in parallel (batch of 10)
    market_ids = [m.get("marketId", "") for m in all_markets if m.get("marketId")]
    if not market_ids:
        return opportunities

    market_books = betfair_client.list_market_books(market_ids)
    books_by_id = {b.get("marketId", ""): b for b in market_books}

    # Catalog info by market ID for titles
    catalog_by_id = {m.get("marketId", ""): m for m in all_markets}

    for market_id, book in books_by_id.items():
        runners = book.get("runners", [])
        if len(runners) < 2:
            continue

        implied_probs = []
        selection_ids = []
        prices_raw = []
        valid = True

        for runner in runners:
            ex = runner.get("ex", {})
            back_prices = ex.get("availableToBack", [])
            if not back_prices:
                valid = False
                break
            best_back = float(back_prices[0].get("price", 0))
            if best_back <= 1.0:
                valid = False
                break
            implied = 1.0 / best_back
            implied_probs.append(implied)
            selection_ids.append(runner.get("selectionId"))
            prices_raw.append(best_back)

        if not valid or not implied_probs:
            continue

        result = net_profit_betfair_backall(implied_probs, config.BETFAIR_COMMISSION_RATE)
        if result["net_profit"] >= min_profit:
            total = sum(implied_probs)
            n = len(implied_probs)
            catalog = catalog_by_id.get(market_id, {})
            market_name = catalog.get("marketName", "Unknown")
            event_info = catalog.get("event", {})
            event_name = event_info.get("name", "")
            title = f"{event_name} - {market_name}" if event_name else market_name

            price_summary = ", ".join(f"{p:.3f}" for p in sorted(implied_probs, reverse=True)[:5])
            if n > 5:
                price_summary += f"... ({n} runners)"

            # Min depth = min stake available across all runners
            min_depth = float("inf")
            for runner in runners:
                ex = runner.get("ex", {})
                backs = ex.get("availableToBack", [])
                if backs:
                    size = float(backs[0].get("size", 0))
                    min_depth = min(min_depth, size)
            if min_depth == float("inf"):
                min_depth = 0

            opportunities.append({
                "type": "BetfairBackAll",
                "market": title[:60],
                "prices": price_summary,
                "total_cost": f"${total:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total * 100:.2f}%",
                "_bf_market_id": market_id,
                "_bf_selection_ids": selection_ids,
                "_bf_prices": implied_probs,
                "_clob_depth": min_depth,
            })

    logger.info("Found %d Betfair back-all opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities


def scan_betfair_backlay(betfair_client: BetfairClient, min_profit: float) -> list[dict]:
    """Scan for Betfair back-lay arbitrage (crossed books on same runner).

    Same runner has back_odds < lay_odds (crossed book).
    """
    opportunities = []

    if not betfair_client or not betfair_client.authenticated:
        return opportunities

    events = betfair_client.list_events()
    if not events:
        return opportunities

    logger.info("Scanning %d Betfair events for back-lay arbs...", len(events))

    # Reuse market fetch logic
    all_markets = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {}
        for e in events[:100]:
            ev_data = e.get("event", {})
            ev_id = ev_data.get("id", "")
            if ev_id:
                futures[pool.submit(betfair_client.list_markets, ev_id)] = e
        for future in as_completed(futures):
            try:
                all_markets.extend(future.result())
            except Exception as e:
                logger.debug("Betfair market fetch failed: %s", e)

    market_ids = [m.get("marketId", "") for m in all_markets if m.get("marketId")]
    if not market_ids:
        return opportunities

    market_books = betfair_client.list_market_books(market_ids)
    catalog_by_id = {m.get("marketId", ""): m for m in all_markets}

    for book in market_books:
        market_id = book.get("marketId", "")
        runners = book.get("runners", [])

        for runner in runners:
            ex = runner.get("ex", {})
            back_prices = ex.get("availableToBack", [])
            lay_prices = ex.get("availableToLay", [])

            if not back_prices or not lay_prices:
                continue

            best_back = float(back_prices[0].get("price", 0))
            best_lay = float(lay_prices[0].get("price", 0))

            if best_back <= 1.0 or best_lay <= 1.0:
                continue

            # Crossed book: back odds > lay odds means we can profit
            if best_back <= best_lay:
                continue

            # Convert to implied probabilities
            back_prob = 1.0 / best_back
            lay_prob = 1.0 / best_lay

            result = net_profit_betfair_backlay(back_prob, lay_prob, config.BETFAIR_COMMISSION_RATE)
            if result["net_profit"] >= min_profit:
                catalog = catalog_by_id.get(market_id, {})
                market_name = catalog.get("marketName", "Unknown")
                event_info = catalog.get("event", {})
                event_name = event_info.get("name", "")
                runner_name = ""
                # Try to get runner name from catalog
                for cat_runner in catalog.get("runners", []):
                    if cat_runner.get("selectionId") == runner.get("selectionId"):
                        runner_name = cat_runner.get("runnerName", "")
                        break
                title = f"{event_name} - {runner_name}" if event_name else runner_name or market_name

                back_size = float(back_prices[0].get("size", 0))
                lay_size = float(lay_prices[0].get("size", 0))

                opportunities.append({
                    "type": "BetfairBackLay",
                    "market": title[:60],
                    "prices": f"back={back_prob:.3f} lay={lay_prob:.3f}",
                    "total_cost": f"${back_prob:.4f}",
                    "gross_spread": f"{result['gross_spread']:.4f}",
                    "fees": f"${result['fees']:.4f}",
                    "net_profit": result["net_profit"],
                    "net_roi": f"{result['net_profit'] / back_prob * 100:.2f}%" if back_prob > 0 else "0%",
                    "_bf_market_id": market_id,
                    "_bf_selection_id": runner.get("selectionId"),
                    "_bf_back_price": back_prob,
                    "_bf_lay_price": lay_prob,
                    "_clob_depth": min(back_size, lay_size),
                })

    logger.info("Found %d Betfair back-lay opportunities.", len(opportunities))
    opportunities = filter_dust(opportunities)
    return opportunities
