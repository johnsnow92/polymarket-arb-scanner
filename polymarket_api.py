"""Polymarket API client for Gamma API and CLOB."""

import json
import time
import requests

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

# Rate limiting
_last_request_time = 0
MIN_REQUEST_INTERVAL = 0.1  # 100ms between requests


def _rate_limit():
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


def fetch_all_markets(limit: int = 500, max_pages: int = 20) -> list[dict]:
    """Fetch all active markets from the Gamma API with pagination."""
    all_markets = []
    offset = 0

    for _ in range(max_pages):
        _rate_limit()
        params = {
            "limit": limit,
            "offset": offset,
            "active": "true",
            "closed": "false",
        }
        try:
            resp = requests.get(f"{GAMMA_BASE}/markets", params=params, timeout=30)
            resp.raise_for_status()
            markets = resp.json()
        except (requests.RequestException, json.JSONDecodeError) as e:
            print(f"  [WARN] Polymarket markets request failed at offset {offset}: {e}")
            break

        if not markets:
            break

        all_markets.extend(markets)
        offset += limit

        if len(markets) < limit:
            break

    return all_markets


def fetch_events(limit: int = 500, max_pages: int = 20) -> list[dict]:
    """Fetch events from the Gamma API (for grouping multi-outcome markets)."""
    all_events = []
    offset = 0

    for _ in range(max_pages):
        _rate_limit()
        params = {
            "limit": limit,
            "offset": offset,
            "active": "true",
            "closed": "false",
        }
        try:
            resp = requests.get(f"{GAMMA_BASE}/events", params=params, timeout=30)
            resp.raise_for_status()
            events = resp.json()
        except (requests.RequestException, json.JSONDecodeError) as e:
            print(f"  [WARN] Polymarket events request failed at offset {offset}: {e}")
            break

        if not events:
            break

        all_events.extend(events)
        offset += limit

        if len(events) < limit:
            break

    return all_events


def fetch_order_book(token_id: str) -> dict | None:
    """Fetch order book from CLOB for a given token ID."""
    _rate_limit()
    try:
        resp = requests.get(
            f"{CLOB_BASE}/book",
            params={"token_id": token_id},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, json.JSONDecodeError) as e:
        print(f"  [WARN] Order book request failed for {token_id}: {e}")
        return None


def get_best_bid_ask(order_book: dict) -> dict:
    """Extract best bid/ask price and size from a CLOB order book response.

    Returns dict with keys: bid, bid_size, ask, ask_size (all float or None).
    """
    result = {"bid": None, "bid_size": None, "ask": None, "ask_size": None}
    bids = order_book.get("bids", [])
    asks = order_book.get("asks", [])
    if bids:
        best_bid = bids[0]  # Highest bid first
        result["bid"] = float(best_bid.get("price", 0))
        result["bid_size"] = float(best_bid.get("size", 0))
    if asks:
        best_ask = asks[0]  # Lowest ask first
        result["ask"] = float(best_ask.get("price", 0))
        result["ask_size"] = float(best_ask.get("size", 0))
    return result


def get_clob_prices(market: dict) -> dict | None:
    """Fetch CLOB order books for a binary market's YES and NO tokens.

    Uses the clobTokenIds field to get both token order books.
    Returns dict: {yes_ask, yes_ask_size, no_ask, no_ask_size,
                   yes_bid, yes_bid_size, no_bid, no_bid_size} or None.
    """
    token_ids_raw = market.get("clobTokenIds")
    if not token_ids_raw:
        return None
    try:
        if isinstance(token_ids_raw, str):
            token_ids = json.loads(token_ids_raw)
        else:
            token_ids = token_ids_raw
    except (json.JSONDecodeError, ValueError):
        return None

    if not token_ids or len(token_ids) < 2:
        return None

    yes_token, no_token = token_ids[0], token_ids[1]

    yes_book = fetch_order_book(yes_token)
    no_book = fetch_order_book(no_token)

    if not yes_book or not no_book:
        return None

    yes_data = get_best_bid_ask(yes_book)
    no_data = get_best_bid_ask(no_book)

    return {
        "yes_ask": yes_data["ask"],
        "yes_ask_size": yes_data["ask_size"],
        "no_ask": no_data["ask"],
        "no_ask_size": no_data["ask_size"],
        "yes_bid": yes_data["bid"],
        "yes_bid_size": yes_data["bid_size"],
        "no_bid": no_data["bid"],
        "no_bid_size": no_data["bid_size"],
    }


def parse_outcome_prices(market: dict) -> list[float] | None:
    """Parse outcomePrices from a market dict. Returns list of floats or None."""
    raw = market.get("outcomePrices")
    if not raw:
        return None
    try:
        if isinstance(raw, str):
            prices = json.loads(raw)
        else:
            prices = raw
        return [float(p) for p in prices]
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def get_binary_markets(markets: list[dict]) -> list[dict]:
    """Filter for binary (YES/NO) markets that are not negRisk."""
    binary = []
    for m in markets:
        if m.get("negRisk"):
            continue
        outcomes = m.get("outcomes")
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except json.JSONDecodeError:
                continue
        if outcomes and len(outcomes) == 2:
            prices = parse_outcome_prices(m)
            if prices and len(prices) == 2:
                binary.append(m)
    return binary


def get_negrisk_events(events: list[dict]) -> list[dict]:
    """Filter events where ALL markets are negRisk with parseable prices.

    An incomplete outcome set (some markets missing negRisk or prices)
    is not a guaranteed arb — we'd be buying into a partial set.
    """
    negrisk_events = []
    for event in events:
        markets = event.get("markets", [])
        if len(markets) < 2:
            continue
        # Require ALL markets in the event to be negRisk with valid prices
        all_negrisk = all(m.get("negRisk") for m in markets)
        all_priced = all(parse_outcome_prices(m) for m in markets)
        if not (all_negrisk and all_priced):
            continue
        negrisk_events.append(event)
    return negrisk_events
