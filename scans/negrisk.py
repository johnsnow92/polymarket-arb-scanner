"""NegRisk internal arbitrage scan (multi-outcome sum < $1.00 on Polymarket)."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from polymarket_api import get_negrisk_events, parse_outcome_prices
from fees import net_profit_negrisk_internal
from scans.helpers import _extract_token_ids, _fetch_clob_for_market, _within_resolution_window, filter_dust, _days_to_resolution

logger = logging.getLogger(__name__)


def _refine_negrisk_with_clob(opportunities: list[dict], events_by_title: dict, min_profit: float) -> list[dict]:
    """Stage 2: Re-check NegRisk candidates using CLOB ask prices."""
    if not opportunities:
        return opportunities

    logger.info("Refining %d NegRisk candidates with CLOB ask prices...", len(opportunities))

    # Pre-fetch all CLOB data for NegRisk markets in parallel
    all_markets = []
    for opp in opportunities:
        event_key = opp.get("_event_key")
        event = events_by_title.get(event_key) if event_key else None
        if event:
            all_markets.extend(event.get("markets", []))

    clob_cache = {}
    if all_markets:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_fetch_clob_for_market, m): m for m in all_markets}
            for future in as_completed(futures):
                try:
                    market, clob = future.result()
                    m_key = id(market)
                    clob_cache[m_key] = clob
                except Exception:
                    pass

    refined = []
    for opp in opportunities:
        event_key = opp.get("_event_key")
        event = events_by_title.get(event_key) if event_key else None
        if not event:
            refined.append(opp)
            continue

        markets = event.get("markets", [])
        yes_asks = []
        min_depth = float("inf")
        clob_count = 0
        total_outcomes = len(markets)

        for m in markets:
            clob = clob_cache.get(id(m))
            if clob and clob["yes_ask"] is not None:
                yes_asks.append(clob["yes_ask"])
                clob_count += 1
                depth = clob["yes_ask_size"] or 0
                min_depth = min(min_depth, depth)
            else:
                # Fall back to mid-price + $0.01 buffer
                prices = parse_outcome_prices(m)
                if prices:
                    mid_price = prices[0]
                    yes_asks.append(mid_price + 0.01)
                else:
                    yes_asks.append(None)

        # Remove entries where we couldn't get any price
        if None in yes_asks:
            opp["_clob_refined"] = False
            refined.append(opp)
            continue

        # Require at least 50% of outcomes to have real CLOB data
        clob_coverage = clob_count / total_outcomes if total_outcomes > 0 else 0
        if clob_coverage < 0.5:
            opp["_clob_refined"] = False
            refined.append(opp)
            continue

        result = net_profit_negrisk_internal(yes_asks)
        if result["net_profit"] >= min_profit:
            total = sum(yes_asks)
            price_summary = ", ".join(f"{p:.3f}" for p in sorted(yes_asks, reverse=True)[:5])
            if len(yes_asks) > 5:
                price_summary += f"... ({len(yes_asks)} total)"
            opp["prices"] = price_summary
            opp["total_cost"] = f"${total:.4f}"
            opp["gross_spread"] = f"{result['gross_spread']:.4f}"
            opp["fees"] = f"${result['fees']:.4f}"
            opp["net_profit"] = result["net_profit"]
            opp["net_roi"] = f"{result['net_profit'] / total * 100:.2f}%"
            opp["_clob_depth"] = min_depth if min_depth != float("inf") else 0
            opp["_partial_clob"] = clob_count < total_outcomes
            opp["_clob_coverage"] = f"{clob_count}/{total_outcomes}"
            refined.append(opp)

    dropped = len(opportunities) - len(refined)
    if dropped:
        logger.info("Dropped %d NegRisk candidates at CLOB ask prices.", dropped)
    return refined


def scan_negrisk_internal(events: list[dict], min_profit: float) -> list[dict]:
    """Scan for NegRisk arbitrage on Polymarket multi-outcome events."""
    opportunities = []
    events_by_title = {}

    negrisk_events = get_negrisk_events(events)
    logger.info("Scanning %d NegRisk events...", len(negrisk_events))

    filtered_resolution = 0
    for event in negrisk_events:
        markets = event.get("markets", [])
        if len(markets) < 2:
            continue

        # Collect YES prices for each outcome
        yes_prices = []
        outcome_labels = []
        valid = True

        for m in markets:
            if not _within_resolution_window(m, platform="polymarket"):
                filtered_resolution += 1
                valid = False
                break
            prices = parse_outcome_prices(m)
            if not prices:
                valid = False
                break
            # For negRisk markets, first price is the YES price for that outcome
            yes_price = prices[0]
            if yes_price <= 0:
                valid = False
                break
            yes_prices.append(yes_price)
            label = m.get("groupItemTitle", m.get("question", "?"))
            outcome_labels.append(label[:20])

        if not valid or not yes_prices:
            continue

        # Sanity check: very low total with many outcomes likely means missing markets
        total_yes = sum(yes_prices)
        if len(yes_prices) >= 5 and total_yes < 0.50:
            event_title = event.get("title", "Unknown")[:60]
            logger.warning("Likely missing outcomes: '%s' (%d outcomes sum to %.3f)",
                          event_title, len(yes_prices), total_yes)

        result = net_profit_negrisk_internal(yes_prices)

        if result["net_profit"] >= min_profit:
            total = sum(yes_prices)
            price_summary = ", ".join(f"{p:.3f}" for p in sorted(yes_prices, reverse=True)[:5])
            if len(yes_prices) > 5:
                price_summary += f"... ({len(yes_prices)} total)"

            event_key = event.get("id", event.get("title", ""))
            events_by_title[event_key] = event
            # Extract token IDs for each outcome market (YES token = index 0)
            negrisk_token_ids = []
            for m in markets:
                tids = _extract_token_ids(m)
                negrisk_token_ids.append(tids[0] if tids else "")
            opportunities.append({
                "type": f"NegRisk({len(yes_prices)})",
                "market": event.get("title", "Unknown")[:60],
                "prices": price_summary,
                "total_cost": f"${total:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total * 100:.2f}%",
                "volume": f"${sum(float(m.get('volume', 0) or 0) for m in markets):,.0f}",
                "_event_key": event_key,
                "_token_ids": negrisk_token_ids,
                "_days_to_resolution": _days_to_resolution(markets[0], "polymarket"),
            })

    if filtered_resolution:
        logger.info("Filtered %d NegRisk events outside resolution window.", filtered_resolution)

    # Stage 2: Refine with CLOB ask prices
    opportunities = _refine_negrisk_with_clob(opportunities, events_by_title, min_profit)

    opportunities = filter_dust(opportunities)

    return opportunities
