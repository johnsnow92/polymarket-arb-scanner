"""Cross-platform arbitrage scans (PM vs Kalshi, and all-platform pairs)."""

import logging
from functools import partial
from concurrent.futures import ThreadPoolExecutor, as_completed

from polymarket_api import get_binary_markets, get_clob_prices, parse_outcome_prices
from kalshi_api import KalshiClient
from matcher import match_markets_to_events, match_markets_to_events_semantic, match_cross_platform, match_cross_platform_semantic, detect_inverted
from config import FUZZY_MATCH_THRESHOLD, SEMANTIC_MATCHING_ENABLED, SEMANTIC_MATCH_THRESHOLD
from fees import (
    net_profit_cross_platform,
    net_profit_cross_betfair,
    net_profit_cross_smarkets,
    net_profit_cross_sxbet,
    net_profit_cross_matchbook,
    net_profit_cross_gemini,
    net_profit_cross_ibkr,
    net_profit_cross_generic,
)
from scans.helpers import _extract_token_ids, _fetch_clob_for_market, _parallel_fetch_kalshi, _within_resolution_window, filter_dust, _days_to_resolution

logger = logging.getLogger(__name__)


def _make_cross_fee(pa: str, pb: str):
    """Create a 4-arg fee function for a platform pair via partial application."""
    return partial(net_profit_cross_generic, platform_a=pa, platform_b=pb)


# All platform names that participate in cross-platform scans
_ALL_PLATFORMS = ["polymarket", "kalshi", "betfair", "smarkets", "sxbet", "matchbook", "gemini", "ibkr"]

# Fee function lookup for cross-platform pairs.
# Polymarket pairs keep their hand-tuned implementations; all other pairs
# use the generic fee calculator.
_CROSS_FEE_FUNCS = {
    ("polymarket", "kalshi"): net_profit_cross_platform,
    ("polymarket", "betfair"): net_profit_cross_betfair,
    ("polymarket", "smarkets"): net_profit_cross_smarkets,
    ("polymarket", "sxbet"): net_profit_cross_sxbet,
    ("polymarket", "matchbook"): net_profit_cross_matchbook,
    ("polymarket", "gemini"): net_profit_cross_gemini,
    ("polymarket", "ibkr"): net_profit_cross_ibkr,
}

# Auto-generate generic fee functions for all non-Polymarket pairs
for _i, _pa in enumerate(_ALL_PLATFORMS):
    for _pb in _ALL_PLATFORMS[_i + 1:]:
        if (_pa, _pb) not in _CROSS_FEE_FUNCS and (_pb, _pa) not in _CROSS_FEE_FUNCS:
            _CROSS_FEE_FUNCS[(_pa, _pb)] = _make_cross_fee(_pa, _pb)


def _refine_cross_with_clob(opportunities: list[dict], markets_by_key: dict, min_profit: float) -> list[dict]:
    """Stage 2: Re-check cross-platform candidates using CLOB ask prices for Polymarket side."""
    if not opportunities:
        return opportunities

    logger.info("Refining %d cross-platform candidates with CLOB ask prices...", len(opportunities))

    # Pre-fetch CLOB prices in parallel
    fetch_tasks = {}  # market_key -> market
    for opp in opportunities:
        market_key = opp.get("_market_key")
        market = markets_by_key.get(market_key) if market_key else None
        if market and market_key not in fetch_tasks:
            fetch_tasks[market_key] = market

    clob_results = {}
    if fetch_tasks:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_fetch_clob_for_market, m): mk
                       for mk, m in fetch_tasks.items()}
            for future in as_completed(futures):
                mk = futures[future]
                try:
                    _, clob = future.result()
                    clob_results[mk] = clob
                except Exception as e:
                    logger.debug("CLOB fetch failed for cross-platform refinement: %s", e)

    refined = []
    for opp in opportunities:
        market_key = opp.get("_market_key")
        market = markets_by_key.get(market_key) if market_key else None
        if not market:
            refined.append(opp)
            continue

        clob = clob_results.get(market_key)
        if not clob:
            opp["_clob_refined"] = False
            refined.append(opp)
            continue

        # Use ask price, fall back to bid + 0.01 if ask is missing
        pm_yes = clob["yes_ask"]
        pm_no = clob["no_ask"]
        partial = False
        if pm_yes is None and clob.get("yes_bid") is not None:
            pm_yes = clob["yes_bid"] + 0.01
            partial = True
        if pm_no is None and clob.get("no_bid") is not None:
            pm_no = clob["no_bid"] + 0.01
            partial = True
        if pm_yes is None or pm_no is None:
            opp["_clob_refined"] = False
            refined.append(opp)
            continue
        k_yes = opp.get("_kalshi_yes")
        k_no = opp.get("_kalshi_no")

        if k_yes is None or k_no is None:
            refined.append(opp)
            continue

        result1 = net_profit_cross_platform(pm_yes, k_no, "yes", "no")
        result2 = net_profit_cross_platform(pm_no, k_yes, "no", "yes")
        best = result1 if result1["net_profit"] > result2["net_profit"] else result2

        if best["net_profit"] >= min_profit:
            if best == result1:
                total_cost = pm_yes + k_no
                opp["prices"] = f"PM_Y={pm_yes:.3f} K_N={k_no:.3f}"
            else:
                total_cost = pm_no + k_yes
                opp["prices"] = f"PM_N={pm_no:.3f} K_Y={k_yes:.3f}"
            opp["total_cost"] = f"${total_cost:.4f}"
            opp["gross_spread"] = f"{best['gross_spread']:.4f}"
            opp["fees"] = f"${best['fees']:.4f}"
            opp["net_profit"] = best["net_profit"]
            opp["net_roi"] = f"{best['net_profit'] / total_cost * 100:.2f}%"
            opp["_clob_depth"] = min(
                clob["yes_ask_size"] or 0,
                clob["no_ask_size"] or 0,
            )
            if partial:
                opp["_partial_clob"] = True
            refined.append(opp)

    dropped = len(opportunities) - len(refined)
    if dropped:
        logger.info("Dropped %d cross-platform candidates at CLOB ask prices.", dropped)
    return refined


def scan_cross_platform(
    poly_markets: list[dict],
    kalshi_client: KalshiClient | None,
    min_profit: float,
    kalshi_markets_by_event: dict | None = None,
    min_confidence: str = "LOW",
    kalshi_events_preloaded: list[dict] | None = None,
) -> list[dict]:
    """Scan for cross-platform arbitrage between Polymarket and Kalshi."""
    opportunities = []
    markets_by_key = {}

    if not kalshi_client:
        logger.info("Kalshi credentials not configured. Skipping cross-platform scan.")
        return opportunities

    kalshi_events = kalshi_events_preloaded
    if kalshi_events is None:
        logger.info("Fetching Kalshi events...")
        kalshi_events = kalshi_client.fetch_all_events()
    if not kalshi_events:
        logger.warning("No Kalshi events fetched.")
        return opportunities

    # Filter Polymarket to binary markets only for cross-platform matching
    binary_poly = get_binary_markets(poly_markets)
    logger.info("Matching %d Polymarket binary markets vs %d Kalshi events...", len(binary_poly), len(kalshi_events))

    if SEMANTIC_MATCHING_ENABLED:
        matched = match_markets_to_events_semantic(
            binary_poly, kalshi_events,
            threshold=SEMANTIC_MATCH_THRESHOLD, min_confidence=min_confidence,
        )
    else:
        matched = match_markets_to_events(
            binary_poly, kalshi_events,
            threshold=FUZZY_MATCH_THRESHOLD, min_confidence=min_confidence,
        )
    logger.info("Found %d event matches. Fetching Kalshi market prices...", len(matched))

    # Pre-fetch Kalshi markets in parallel if not already done
    if kalshi_markets_by_event is None:
        tickers = [m["kalshi_event"].get("event_ticker", "") for m in matched if m["kalshi_event"].get("event_ticker")]
        kalshi_markets_by_event = _parallel_fetch_kalshi(kalshi_client, tickers)

    filtered_resolution = 0
    for i, match in enumerate(matched):
        pm = match["polymarket"]
        ke = match["kalshi_event"]

        if not _within_resolution_window(pm, platform="polymarket"):
            filtered_resolution += 1
            continue

        # Get Polymarket prices
        pm_prices = parse_outcome_prices(pm)
        if not pm_prices or len(pm_prices) != 2:
            continue
        pm_yes, pm_no = pm_prices[0], pm_prices[1]

        # Use pre-fetched Kalshi markets
        event_ticker = ke.get("event_ticker", "")
        k_markets = kalshi_markets_by_event.get(event_ticker, [])
        if not k_markets:
            continue

        # Find the best opportunity across all Kalshi sub-markets in this event
        best_opp = None

        for km in k_markets:
            if not _within_resolution_window(km, platform="kalshi"):
                continue
            k_yes, k_no = kalshi_client.get_market_price(km)
            if k_yes is None or k_no is None:
                continue

            # Check for inversion
            pm_title = pm.get("question", pm.get("title", ""))
            k_title = km.get("title", "")
            inverted = detect_inverted(pm_title, k_title)
            if inverted:
                k_yes, k_no = k_no, k_yes

            # Strategy 1: Buy PM YES + Kalshi NO
            result1 = net_profit_cross_platform(pm_yes, k_no, "yes", "no")
            # Strategy 2: Buy PM NO + Kalshi YES
            result2 = net_profit_cross_platform(pm_no, k_yes, "no", "yes")

            best = result1 if result1["net_profit"] > result2["net_profit"] else result2
            if best == result1:
                strategy = "PM_YES + K_NO"
                total_cost = pm_yes + k_no
                prices_str = f"PM_Y={pm_yes:.3f} K_N={k_no:.3f}"
                best_k_yes, best_k_no = k_yes, k_no
            else:
                strategy = "PM_NO + K_YES"
                total_cost = pm_no + k_yes
                prices_str = f"PM_N={pm_no:.3f} K_Y={k_yes:.3f}"
                best_k_yes, best_k_no = k_yes, k_no

            if best["net_profit"] >= min_profit and total_cost > 0:
                if best_opp is None or best["net_profit"] > best_opp["net_profit"]:
                    sim = match["similarity"]
                    market_key = pm.get("conditionId", pm.get("question", ""))
                    markets_by_key[market_key] = pm
                    pm_token_ids = _extract_token_ids(pm)
                    if best == result1:
                        _pa_price, _pb_price = pm_yes, k_no
                        _pa_side, _pb_side = "yes", "no"
                    else:
                        _pa_price, _pb_price = pm_no, k_yes
                        _pa_side, _pb_side = "no", "yes"
                    best_opp = {
                        "type": f"Cross({strategy})",
                        "market": pm_title[:50],
                        "kalshi": k_title[:50],
                        "match": f"{sim}%",
                        "prices": prices_str,
                        "total_cost": f"${total_cost:.4f}",
                        "gross_spread": f"{best['gross_spread']:.4f}",
                        "fees": f"${best['fees']:.4f}",
                        "net_profit": best["net_profit"],
                        "net_roi": f"{best['net_profit'] / total_cost * 100:.2f}%",
                        "volume": f"${float(pm.get('volume', 0) or 0):,.0f}",
                        "_market_key": market_key,
                        "_kalshi_yes": best_k_yes,
                        "_kalshi_no": best_k_no,
                        "_kalshi_ticker": km.get("ticker", ""),
                        "_token_ids": pm_token_ids,
                        "confidence": match.get("confidence", "LOW"),
                        "_days_to_resolution": _days_to_resolution(pm, "polymarket"),
                        "_platform_a": "polymarket",
                        "_platform_b": "kalshi",
                        "_price_a": _pa_price,
                        "_price_b": _pb_price,
                        "_side_a": _pa_side,
                        "_side_b": _pb_side,
                    }

        if best_opp:
            opportunities.append(best_opp)

        if (i + 1) % 50 == 0:
            logger.info("Processed %d/%d matches...", i + 1, len(matched))

    if filtered_resolution:
        logger.info("Filtered %d/%d cross-platform matches outside resolution window.", filtered_resolution, len(matched))

    # Stage 2: Refine with CLOB ask prices
    opportunities = _refine_cross_with_clob(opportunities, markets_by_key, min_profit)

    opportunities = filter_dust(opportunities)

    return opportunities


def _attach_exec_metadata(opp: dict, market: dict, platform: str, suffix: str):
    """Attach platform-specific execution metadata to a cross-all opportunity."""
    if platform == "polymarket":
        opp["_token_ids"] = _extract_token_ids(market)
    elif platform == "kalshi":
        opp["_kalshi_ticker"] = market.get("ticker", "")
    elif platform == "betfair":
        opp["_market_id"] = market.get("marketId", "")
        runners = market.get("runners", [])
        if runners:
            opp["_selection_id"] = runners[0].get("selectionId")
    elif platform == "smarkets":
        opp["_sm_market_id"] = market.get("id", "")
    elif platform == "sxbet":
        opp["_sx_market_hash"] = market.get("marketHash", market.get("id", ""))
    elif platform == "matchbook":
        opp["_mb_market_id"] = market.get("id", "")
        runners = market.get("runners", [])
        if runners:
            opp["_mb_runner_id"] = runners[0].get("id")
    elif platform == "gemini":
        opp["_gm_event_id"] = market.get("id", "")
        contracts = market.get("contracts", [])
        for c in contracts:
            label = (c.get("label") or c.get("outcome") or "").lower()
            if "yes" in label:
                opp["_gm_yes_symbol"] = c.get("instrumentSymbol", "")
            elif "no" in label:
                opp["_gm_no_symbol"] = c.get("instrumentSymbol", "")
    elif platform == "ibkr":
        opp["_ibkr_event_id"] = market.get("id", "")
        contracts = market.get("contracts", [])
        for c in contracts:
            side = (c.get("side") or c.get("label") or "").upper()
            if "YES" in side:
                opp["_ibkr_yes_conid"] = c.get("conid", "")
            elif "NO" in side:
                opp["_ibkr_no_conid"] = c.get("conid", "")


def scan_cross_all(
    poly_markets: list[dict],
    platform_clients: dict,
    min_profit: float,
    min_confidence: str = "LOW",
) -> list[dict]:
    """Scan for cross-platform arbitrage across all platform pairs."""
    opportunities = []
    binary_poly = get_binary_markets(poly_markets) if poly_markets else []

    # Build list of all platform market sets
    platform_markets = {"polymarket": binary_poly}
    for name, (client, markets) in platform_clients.items():
        if markets:
            platform_markets[name] = markets

    platforms = list(platform_markets.keys())
    logger.info("Cross-all: matching across %d platforms: %s", len(platforms), ", ".join(platforms))

    # Compare all N*(N-1)/2 pairs
    for i, pa in enumerate(platforms):
        for pb in platforms[i + 1:]:
            markets_a = platform_markets[pa]
            markets_b = platform_markets[pb]
            if not markets_a or not markets_b:
                continue

            logger.info("Matching %s (%d) vs %s (%d)...", pa, len(markets_a), pb, len(markets_b))
            if SEMANTIC_MATCHING_ENABLED:
                matched = match_cross_platform_semantic(
                    markets_a, markets_b, pa, pb,
                    threshold=SEMANTIC_MATCH_THRESHOLD, min_confidence=min_confidence,
                )
            else:
                matched = match_cross_platform(
                    markets_a, markets_b, pa, pb,
                    threshold=FUZZY_MATCH_THRESHOLD, min_confidence=min_confidence,
                )
            logger.info("Found %d matches between %s and %s", len(matched), pa, pb)

            # Determine fee function
            fee_key = (pa, pb)
            if fee_key not in _CROSS_FEE_FUNCS:
                fee_key = (pb, pa)
            fee_func = _CROSS_FEE_FUNCS.get(fee_key)
            if not fee_func:
                logger.debug("No fee function for pair %s-%s, skipping", pa, pb)
                continue

            for m in matched:
                ma = m["market_a"]
                mb = m["market_b"]

                # Get prices from each platform's client
                client_a = platform_clients.get(pa, (None, None))[0] if pa != "polymarket" else None
                client_b = platform_clients.get(pb, (None, None))[0] if pb != "polymarket" else None

                if pa == "polymarket":
                    pm_prices = parse_outcome_prices(ma)
                    if not pm_prices or len(pm_prices) != 2:
                        continue
                    a_yes, a_no = pm_prices[0], pm_prices[1]
                else:
                    a_yes, a_no = client_a.get_market_price(ma) if client_a else (None, None)

                if pb == "polymarket":
                    pm_prices = parse_outcome_prices(mb)
                    if not pm_prices or len(pm_prices) != 2:
                        continue
                    b_yes, b_no = pm_prices[0], pm_prices[1]
                else:
                    b_yes, b_no = client_b.get_market_price(mb) if client_b else (None, None)

                if a_yes is None or a_no is None or b_yes is None or b_no is None:
                    continue

                # Check for inversion (one title negated, other not)
                a_title = m.get("title_a", "")
                b_title = m.get("title_b", "")
                if detect_inverted(a_title, b_title):
                    b_yes, b_no = b_no, b_yes

                # Check both strategies: A_YES + B_NO, A_NO + B_YES
                result1 = fee_func(a_yes, b_no, "yes", "no")
                result2 = fee_func(a_no, b_yes, "no", "yes")
                best = result1 if result1["net_profit"] > result2["net_profit"] else result2

                if best["net_profit"] >= min_profit:
                    if best == result1:
                        total_cost = a_yes + b_no
                        prices_str = f"{pa}_Y={a_yes:.3f} {pb}_N={b_no:.3f}"
                        _pa_price, _pb_price = a_yes, b_no
                        _pa_side, _pb_side = "yes", "no"
                    else:
                        total_cost = a_no + b_yes
                        prices_str = f"{pa}_N={a_no:.3f} {pb}_Y={b_yes:.3f}"
                        _pa_price, _pb_price = a_no, b_yes
                        _pa_side, _pb_side = "no", "yes"

                    if total_cost <= 0:
                        continue

                    opp_entry = {
                        "type": f"Cross({pa[:2].upper()}-{pb[:2].upper()})",
                        "market": m["title_a"][:50],
                        "kalshi": m["title_b"][:50],
                        "match": f"{m['similarity']}%",
                        "prices": prices_str,
                        "total_cost": f"${total_cost:.4f}",
                        "gross_spread": f"{best['gross_spread']:.4f}",
                        "fees": f"${best['fees']:.4f}",
                        "net_profit": best["net_profit"],
                        "net_roi": f"{best['net_profit'] / total_cost * 100:.2f}%",
                        "confidence": m["confidence"],
                        "_platform_a": pa,
                        "_platform_b": pb,
                        "_price_a": _pa_price,
                        "_price_b": _pb_price,
                        "_side_a": _pa_side,
                        "_side_b": _pb_side,
                        "_days_to_resolution": _days_to_resolution(ma, pa),
                    }

                    # Attach execution metadata per platform
                    _attach_exec_metadata(opp_entry, ma, pa, "a")
                    _attach_exec_metadata(opp_entry, mb, pb, "b")

                    opportunities.append(opp_entry)

    # Stage 2: Refine Polymarket side with CLOB ask prices
    _refine_cross_all_with_clob(opportunities, min_profit)

    opportunities = filter_dust(opportunities)

    return opportunities


def _refine_cross_all_with_clob(opportunities: list[dict], min_profit: float):
    """Refine cross-all opportunities that include Polymarket using CLOB ask prices."""
    pm_opps = [o for o in opportunities
               if o.get("_platform_a") == "polymarket" or o.get("_platform_b") == "polymarket"]
    if not pm_opps:
        return

    # Pre-fetch CLOB prices in parallel for all PM-side token IDs
    def _fetch_clob_for_tokens(token_ids):
        return get_clob_prices({"clobTokenIds": token_ids})

    clob_cache = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {}
        for o in pm_opps:
            token_ids = o.get("_token_ids", [])
            if token_ids and len(token_ids) >= 2:
                key = tuple(token_ids[:2])
                if key not in futures:
                    futures[pool.submit(_fetch_clob_for_tokens, list(key))] = key
        for future in as_completed(futures):
            key = futures[future]
            try:
                clob_cache[key] = future.result()
            except Exception as e:
                logger.debug("CLOB fetch failed for cross-all refinement: %s", e)

    refined_out = []
    for o in pm_opps:
        token_ids = o.get("_token_ids", [])
        if not token_ids or len(token_ids) < 2:
            continue

        clob = clob_cache.get(tuple(token_ids[:2]))
        if not clob or clob["yes_ask"] is None or clob["no_ask"] is None:
            continue

        pa = o.get("_platform_a", "")
        pb = o.get("_platform_b", "")
        fee_key = (pa, pb) if (pa, pb) in _CROSS_FEE_FUNCS else (pb, pa)
        ff = _CROSS_FEE_FUNCS.get(fee_key)
        if not ff:
            continue

        pm_yes = clob["yes_ask"]
        pm_no = clob["no_ask"]

        # Parse the other platform's price from the prices string
        other_price = None
        for part in o.get("prices", "").split():
            if not part.startswith("polymarket") and "=" in part:
                try:
                    other_price = float(part.split("=")[1])
                except ValueError as e:
                    logger.debug("Price parse failed in cross-all refinement: %s", e)

        if other_price is None:
            continue

        # Determine which side Polymarket is on
        if pa == "polymarket":
            r1 = ff(pm_yes, other_price, "yes", "no")
            r2 = ff(pm_no, other_price, "no", "yes")
        else:
            r1 = ff(other_price, pm_yes, "yes", "no")
            r2 = ff(other_price, pm_no, "no", "yes")

        best_r = r1 if r1["net_profit"] > r2["net_profit"] else r2
        if best_r["net_profit"] >= min_profit:
            o["net_profit"] = best_r["net_profit"]
            o["fees"] = f"${best_r['fees']:.4f}"
            o["_clob_depth"] = min(clob["yes_ask_size"] or 0, clob["no_ask_size"] or 0)
