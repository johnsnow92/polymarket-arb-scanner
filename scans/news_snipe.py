"""News-driven resolution sniping strategy using Finnhub news headlines."""

import logging
import time
from thefuzz import fuzz

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sentiment keyword definitions
# ---------------------------------------------------------------------------

YES_KEYWORDS = [
    "approved",
    "confirmed",
    "passed",
    "granted",
    "successful",
    "adopted",
    "launched",
    "completed",
]

NO_KEYWORDS = [
    "rejected",
    "failed",
    "denied",
    "blocked",
    "withdrawn",
    "cancelled",
    "delayed",
]


# ---------------------------------------------------------------------------
# Stage 1: News scanning and signal extraction
# ---------------------------------------------------------------------------


def scan_news_snipe(
    markets_by_key: dict,
    finnhub_client,
    cooldown_cache: dict | None = None,
    fuzzy_threshold: int = 70,
) -> list[dict]:
    """Stage 1: Scan news headlines and extract trading signals.

    Fetches recent news, matches to markets, scores sentiment, and applies cooldown.

    Args:
        markets_by_key: Dict of market_key -> market dict (with 'question' field).
        finnhub_client: FinnhubNewsClient instance for fetching news.
        cooldown_cache: Dict tracking market_key -> last_execution_time for deduplication.
        fuzzy_threshold: Fuzzy match threshold (0-100, default 70).

    Returns:
        List of opportunity dicts with type='NewsSnipe', _headline, _sentiment, _confidence.
    """
    if cooldown_cache is None:
        cooldown_cache = {}

    opportunities = []
    current_time = time.time()

    # Build list of symbols from markets (simplified: extract from market titles)
    symbols = _extract_symbols_from_markets(markets_by_key)

    # Fetch headlines for each symbol
    all_headlines = []
    for symbol in symbols[:10]:  # Limit to 10 symbols to avoid rate limits
        try:
            from datetime import datetime, timedelta
            to_date = datetime.utcnow().strftime("%Y-%m-%d")
            from_date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
            headlines = finnhub_client.fetch_company_news(symbol, from_date, to_date)
            all_headlines.extend(headlines)
        except Exception as e:
            logger.warning("Failed to fetch news for %s: %s", symbol, str(e))
            continue

    # Extract signals from headlines
    signals = extract_news_signals(all_headlines, markets_by_key, fuzzy_threshold)

    # Apply cooldown filter
    for signal in signals:
        market_key = signal.get("_market_key", "")
        if market_key in cooldown_cache:
            if cooldown_cache[market_key] > current_time:
                logger.info(
                    "NewsSnipe: cooldown active for %s, skipping",
                    market_key,
                )
                continue

        opportunities.append(signal)

    logger.info(
        "NewsSnipe: matched %d headlines to %d opportunities",
        len(all_headlines),
        len(opportunities),
    )

    return opportunities


def extract_news_signals(
    headlines: list[dict],
    markets_by_key: dict,
    fuzzy_threshold: int = 70,
) -> list[dict]:
    """Extract trading signals from news headlines via fuzzy matching.

    For each headline, attempts to match to a market question. If matched and
    sentiment is detected, returns opportunity.

    Args:
        headlines: List of headline dicts from Finnhub (headline, summary, url, datetime).
        markets_by_key: Dict of market_key -> market dict.
        fuzzy_threshold: Fuzzy match threshold.

    Returns:
        List of signal dicts with _sentiment, _confidence, _headline, _market_key.
    """
    signals = []

    for headline in headlines:
        headline_text = headline.get("headline", "")
        summary_text = headline.get("summary", "")
        full_text = (headline_text + " " + summary_text).lower()

        if not headline_text:
            continue

        # Try to match to a market
        for market_key, market in markets_by_key.items():
            market_question = market.get("question", "").lower()
            if not market_question:
                continue

            # Fuzzy match headline to market question
            similarity = fuzz.token_set_ratio(headline_text.lower(), market_question)
            if similarity < fuzzy_threshold:
                continue

            # Score sentiment
            sentiment_result = _score_sentiment(full_text)
            if sentiment_result["sentiment"] is None:
                continue

            # Record signal
            signal = {
                "type": "NewsSnipe",
                "market": market.get("question", ""),
                "_headline": headline_text,
                "_sentiment": sentiment_result["sentiment"],
                "_confidence": sentiment_result["confidence"],
                "_market_key": market_key,
            }
            signals.append(signal)
            logger.info(
                "Sentiment matched: %s in '%s' (confidence: %.2f)",
                sentiment_result["sentiment"],
                headline_text[:100],
                sentiment_result["confidence"],
            )
            break  # One market per headline

    return signals


def _score_sentiment(text: str) -> dict:
    """Score sentiment of text based on keyword presence.

    Searches for YES and NO keywords in the text. Returns first match found.
    Confidence: 0.8 if keyword in headline, 0.6 if elsewhere.

    Args:
        text: Full text (headline + summary).

    Returns:
        Dict with 'sentiment' (YES/NO/None) and 'confidence' (0.0-1.0).
    """
    # Search for YES keywords first
    for keyword in YES_KEYWORDS:
        if keyword in text:
            return {"sentiment": "YES", "confidence": 0.8}

    # Then search for NO keywords
    for keyword in NO_KEYWORDS:
        if keyword in text:
            return {"sentiment": "NO", "confidence": 0.8}

    return {"sentiment": None, "confidence": 0.0}


# ---------------------------------------------------------------------------
# Stage 2: Refinement and validation
# ---------------------------------------------------------------------------


def _refine_news_with_confidence(
    opportunities: list[dict],
    confidence_floor: float = 0.5,
    cooldown_cache: dict | None = None,
) -> list[dict]:
    """Stage 2: Refine news snipe opportunities by confidence threshold.

    Filters out low-confidence matches and applies cooldown logic.

    Args:
        opportunities: List of opportunities from stage 1.
        confidence_floor: Minimum confidence to keep (default 0.5).
        cooldown_cache: Dict tracking market_key -> last_execution_time.

    Returns:
        Refined list of opportunities.
    """
    if cooldown_cache is None:
        cooldown_cache = {}

    refined = []
    current_time = time.time()

    for opp in opportunities:
        confidence = opp.get("_confidence", 0.0)

        # Filter by confidence threshold
        if confidence < confidence_floor:
            logger.info(
                "Rejected opportunity: confidence %.2f < %.2f threshold",
                confidence,
                confidence_floor,
            )
            continue

        # Check cooldown
        market_key = opp.get("_market_key", "")
        if market_key in cooldown_cache:
            if cooldown_cache[market_key] > current_time:
                logger.info("Opportunity on cooldown: %s", market_key)
                continue

        refined.append(opp)

    logger.info(
        "News refined: %d/%d opportunities passed confidence threshold",
        len(refined),
        len(opportunities),
    )

    return refined


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _extract_symbols_from_markets(markets_by_key: dict) -> list[str]:
    """Extract stock symbols from market questions.

    Simple heuristic: look for capital letters that appear alone or in pairs
    in the question text.

    Args:
        markets_by_key: Dict of markets.

    Returns:
        List of symbols (up to 10 for rate limit safety).
    """
    symbols = set()

    for market in markets_by_key.values():
        question = market.get("question", "")

        # Simple extraction: look for capitalized words of 1-5 chars
        import re
        candidates = re.findall(r"\b[A-Z]{1,5}\b", question)
        for candidate in candidates:
            if len(candidate) >= 1 and candidate not in ("THE", "AND", "FOR", "WITH"):
                symbols.add(candidate)

    return list(symbols)[:10]
