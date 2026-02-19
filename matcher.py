"""Cross-platform market matcher using fuzzy string matching.

Matches Polymarket binary markets against Kalshi events by title similarity.
Kalshi events have clean titles suitable for matching, while their markets
endpoint is dominated by sports parlay combos with unhelpful titles.
"""

import re
from thefuzz import fuzz

# Common filler words that don't help distinguish markets
_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "by", "to", "for", "and", "or",
    "be", "will", "does", "is", "can", "has", "have", "are", "was", "were",
    "do", "did", "been", "being", "this", "that", "it", "its", "with",
    "from", "at", "as", "but", "not", "no", "any", "all", "each", "every",
    "before", "after", "during", "than", "more", "most", "least", "new",
}


def normalize_title(title: str) -> str:
    """Normalize a market/event title for better matching."""
    title = title.lower().strip()
    # Remove markdown formatting
    title = re.sub(r"\*+", "", title)
    # Remove Gemini instrument prefixes (e.g. "GEMI-", "GM-")
    title = re.sub(r"^(gemi-|gm-)", "", title)
    # Remove IBKR ForecastEx prefixes (e.g. "FX-", "IBKR-")
    title = re.sub(r"^(fx-|ibkr-|forecastex\s*[-:]\s*)", "", title)
    # Remove trailing question marks and punctuation
    title = re.sub(r"[?!.]+$", "", title)
    # Normalize whitespace
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _extract_entities(title: str) -> set[str]:
    """Extract key named entities and distinguishing terms from a title.

    Returns significant words (proper nouns, specific terms, numbers)
    that help identify what the market is actually about.
    """
    normalized = title.lower()
    # Split into words, remove stopwords
    words = re.findall(r"\b[a-z0-9]+\b", normalized)
    entities = set()
    for w in words:
        if w not in _STOPWORDS and len(w) >= 3:
            entities.add(w)
    return entities


def classify_confidence(similarity: int, entity_overlap: int, min_entities: int) -> str:
    """Classify match confidence as HIGH, MEDIUM, or LOW.

    HIGH:   similarity >= 90 AND entity overlap >= 60% of smaller entity set
    MEDIUM: similarity >= 80 AND entity overlap >= 40%
    LOW:    everything else that passed threshold
    """
    if min_entities > 0:
        overlap_ratio = entity_overlap / min_entities
    else:
        overlap_ratio = 0

    if similarity >= 90 and overlap_ratio >= 0.60:
        return "HIGH"
    elif similarity >= 80 and overlap_ratio >= 0.40:
        return "MEDIUM"
    return "LOW"


# Confidence tier ordering for filtering
CONFIDENCE_ORDER = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


def match_markets_to_events(
    polymarket_markets: list[dict],
    kalshi_events: list[dict],
    threshold: int = 80,
    min_confidence: str = "LOW",
) -> list[dict]:
    """Match Polymarket markets to Kalshi events by title similarity.

    Uses a two-stage filter:
    1. Fuzzy string similarity (token_sort_ratio >= threshold)
    2. Entity overlap validation (must share key terms, not just filler words)
    3. Confidence classification (HIGH/MEDIUM/LOW)

    Returns list of matched pairs with similarity scores and confidence.
    """
    matches = []
    min_conf_level = CONFIDENCE_ORDER.get(min_confidence.upper(), 1)

    # Pre-normalize Kalshi event titles and extract entities
    kalshi_prepared = []
    for ke in kalshi_events:
        title = ke.get("title", "")
        if not title:
            continue
        norm = normalize_title(title)
        entities = _extract_entities(title)
        kalshi_prepared.append((norm, entities, ke))

    for pm in polymarket_markets:
        pm_title = pm.get("question", "") or pm.get("title", "")
        pm_norm = normalize_title(pm_title)
        pm_entities = _extract_entities(pm_title)

        if not pm_norm or len(pm_norm) < 8:
            continue

        best_score = 0
        best_kalshi_event = None
        best_entity_overlap = 0
        best_min_entities = 0

        for k_norm, k_entities, ke in kalshi_prepared:
            if not k_norm:
                continue

            # Quick reject: if no entity overlap at all, skip
            overlap = pm_entities & k_entities
            if len(overlap) < 2:
                continue

            # Use token_sort_ratio for order-independent matching
            score = fuzz.token_sort_ratio(pm_norm, k_norm)

            # Fallback: try partial_ratio if token_sort is borderline
            if score < threshold and score >= threshold - 15:
                partial = fuzz.partial_ratio(pm_norm, k_norm)
                score = max(score, partial)

            # Require meaningful entity overlap proportional to title length
            min_entities = min(len(pm_entities), len(k_entities))
            if min_entities > 0:
                entity_ratio = len(overlap) / min_entities
            else:
                entity_ratio = 0

            # Combined score: fuzzy match + entity overlap bonus
            combined = score + (entity_ratio * 15)

            # Category scoring boost from Kalshi
            k_category = ke.get("category", "").lower()
            if k_category:
                category_words = set(k_category.replace("-", " ").split())
                if category_words & pm_entities:
                    combined += 5

            if combined > best_score:
                best_score = combined
                best_kalshi_event = ke
                best_entity_overlap = len(overlap)
                best_min_entities = min_entities

        # Require both fuzzy similarity AND meaningful entity overlap
        if best_score >= threshold and best_kalshi_event and best_entity_overlap >= 2:
            confidence = classify_confidence(
                int(best_score), best_entity_overlap, best_min_entities
            )
            # Filter by minimum confidence
            if CONFIDENCE_ORDER.get(confidence, 0) < min_conf_level:
                continue
            matches.append({
                "polymarket": pm,
                "kalshi_event": best_kalshi_event,
                "similarity": int(best_score),
                "entity_overlap": best_entity_overlap,
                "confidence": confidence,
                "pm_title": pm_title,
                "kalshi_title": best_kalshi_event.get("title", ""),
            })

    # Sort by similarity descending
    matches.sort(key=lambda x: x["similarity"], reverse=True)

    # Deduplicate: only keep the best Polymarket match per Kalshi event
    seen_kalshi = set()
    deduped = []
    for m in matches:
        k_ticker = m["kalshi_event"].get("event_ticker", "")
        if k_ticker not in seen_kalshi:
            seen_kalshi.add(k_ticker)
            deduped.append(m)

    return deduped


def _get_title(market: dict) -> str:
    """Extract the best title from a market dict regardless of platform."""
    return (
        market.get("question", "")
        or market.get("title", "")
        or market.get("name", "")
        or market.get("shortName", "")
        or ""
    )


def _get_market_id(market: dict) -> str:
    """Extract a unique identifier from a market dict regardless of platform."""
    return (
        market.get("conditionId", "")
        or market.get("ticker", "")
        or market.get("event_ticker", "")
        or market.get("id", "")
        or market.get("slug", "")
        or _get_title(market)
    )


def match_cross_platform(
    markets_a: list[dict],
    markets_b: list[dict],
    platform_a: str,
    platform_b: str,
    threshold: int = 80,
    min_confidence: str = "LOW",
) -> list[dict]:
    """Platform-agnostic cross-platform matching between any two market lists.

    Args:
        markets_a: List of market dicts from platform A.
        markets_b: List of market dicts from platform B.
        platform_a: Name of platform A (e.g. "polymarket").
        platform_b: Name of platform B (e.g. "kalshi").
        threshold: Minimum combined score for a match.
        min_confidence: Minimum confidence tier (HIGH/MEDIUM/LOW).

    Returns:
        List of matched pairs with similarity scores.
    """
    matches = []
    min_conf_level = CONFIDENCE_ORDER.get(min_confidence.upper(), 1)

    # Pre-normalize platform B markets
    b_prepared = []
    for mb in markets_b:
        title = _get_title(mb)
        if not title:
            continue
        norm = normalize_title(title)
        entities = _extract_entities(title)
        b_prepared.append((norm, entities, mb))

    for ma in markets_a:
        a_title = _get_title(ma)
        a_norm = normalize_title(a_title)
        a_entities = _extract_entities(a_title)

        if not a_norm or len(a_norm) < 8:
            continue

        best_score = 0
        best_match = None
        best_entity_overlap = 0
        best_min_entities = 0

        for b_norm, b_entities, mb in b_prepared:
            if not b_norm:
                continue

            overlap = a_entities & b_entities
            if len(overlap) < 2:
                continue

            score = fuzz.token_sort_ratio(a_norm, b_norm)

            # Fallback: try partial_ratio if token_sort is borderline
            if score < threshold and score >= threshold - 15:
                partial = fuzz.partial_ratio(a_norm, b_norm)
                score = max(score, partial)

            min_entities = min(len(a_entities), len(b_entities))
            entity_ratio = len(overlap) / min_entities if min_entities > 0 else 0
            combined = score + (entity_ratio * 15)

            if combined > best_score:
                best_score = combined
                best_match = mb
                best_entity_overlap = len(overlap)
                best_min_entities = min_entities

        if best_score >= threshold and best_match and best_entity_overlap >= 2:
            confidence = classify_confidence(
                int(best_score), best_entity_overlap, best_min_entities
            )
            if CONFIDENCE_ORDER.get(confidence, 0) < min_conf_level:
                continue
            matches.append({
                "market_a": ma,
                "market_b": best_match,
                "platform_a": platform_a,
                "platform_b": platform_b,
                "similarity": int(best_score),
                "entity_overlap": best_entity_overlap,
                "confidence": confidence,
                "title_a": a_title,
                "title_b": _get_title(best_match),
            })

    matches.sort(key=lambda x: x["similarity"], reverse=True)

    # Deduplicate: best match per platform B market
    seen_b = set()
    deduped = []
    for m in matches:
        b_id = _get_market_id(m["market_b"])
        if b_id not in seen_b:
            seen_b.add(b_id)
            deduped.append(m)

    return deduped


_INVERSION_SIGNALS = [
    "not", "won't", "wouldn't", "cannot", "can't",
    "below", "under", "less than", "fewer",
    "fail to", "fails to", "decline", "drop below",
    "lose", "loses", "defeat", "defeats", "against",
    "no longer", "never", "neither",
]


def detect_inverted(pm_title: str, kalshi_title: str) -> bool:
    """Detect if a Kalshi market is inverted relative to Polymarket.

    Some markets may be phrased oppositely:
    - Polymarket: "Will X happen?" (YES = it happens)
    - Kalshi: "X will NOT happen" (YES = it doesn't happen)

    Uses XOR logic: inversion only if one title has negation, not both.
    """
    pm_lower = pm_title.lower()
    kalshi_lower = kalshi_title.lower()

    def _has_inversion(text):
        return any(f" {signal} " in f" {text} " for signal in _INVERSION_SIGNALS)

    pm_inv = _has_inversion(pm_lower)
    kalshi_inv = _has_inversion(kalshi_lower)

    # XOR: inversion only if exactly one title has negation signals
    return pm_inv != kalshi_inv
