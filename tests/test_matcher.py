"""Tests for matcher.py — cross-platform market matching."""

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from matcher import (
    normalize_title,
    _extract_entities,
    classify_confidence,
    match_markets_to_events,
    match_cross_platform,
    detect_inverted,
)


# ---------------------------------------------------------------------------
# normalize_title
# ---------------------------------------------------------------------------

class TestNormalizeTitle:
    def test_lowercases(self):
        assert normalize_title("Will Biden Win?") == "will biden win"

    def test_strips_whitespace(self):
        assert normalize_title("  hello world  ") == "hello world"

    def test_removes_trailing_punctuation(self):
        assert normalize_title("Will this happen?") == "will this happen"
        assert normalize_title("Breaking news!!!") == "breaking news"
        assert normalize_title("Period.") == "period"

    def test_removes_markdown_formatting(self):
        assert normalize_title("**bold** and *italic*") == "bold and italic"

    def test_normalizes_multiple_spaces(self):
        assert normalize_title("too   many    spaces") == "too many spaces"

    def test_empty_string(self):
        assert normalize_title("") == ""

    def test_only_punctuation(self):
        assert normalize_title("???") == ""


# ---------------------------------------------------------------------------
# _extract_entities
# ---------------------------------------------------------------------------

class TestExtractEntities:
    def test_removes_stopwords(self):
        entities = _extract_entities("Will the market go up")
        assert "will" not in entities
        assert "the" not in entities
        assert "market" in entities

    def test_filters_short_words(self):
        entities = _extract_entities("an ox is up by 10")
        # "an", "ox", "is", "up", "by" are all <= 2 chars or stopwords
        assert "ox" not in entities  # 2 chars, too short
        assert "10" not in entities  # 2 chars

    def test_extracts_meaningful_terms(self):
        entities = _extract_entities("Bitcoin price above 100000 by December 2025")
        assert "bitcoin" in entities
        assert "price" in entities
        assert "100000" in entities
        assert "december" in entities
        assert "2025" in entities

    def test_empty_string(self):
        entities = _extract_entities("")
        assert len(entities) == 0

    def test_only_stopwords(self):
        entities = _extract_entities("the and or but is")
        assert len(entities) == 0


# ---------------------------------------------------------------------------
# classify_confidence
# ---------------------------------------------------------------------------

class TestClassifyConfidence:
    def test_high_confidence(self):
        # similarity >= 90, overlap_ratio >= 0.60
        result = classify_confidence(similarity=95, entity_overlap=4, min_entities=5)
        assert result == "HIGH"

    def test_medium_confidence(self):
        # similarity >= 80, overlap_ratio >= 0.40
        result = classify_confidence(similarity=85, entity_overlap=3, min_entities=5)
        assert result == "MEDIUM"

    def test_low_confidence(self):
        # Below medium thresholds
        result = classify_confidence(similarity=75, entity_overlap=1, min_entities=5)
        assert result == "LOW"

    def test_high_similarity_low_overlap_is_medium_or_low(self):
        # similarity=95 but overlap_ratio < 0.60
        result = classify_confidence(similarity=95, entity_overlap=1, min_entities=5)
        # overlap_ratio = 0.20 < 0.40 -> LOW
        assert result == "LOW"

    def test_zero_min_entities(self):
        result = classify_confidence(similarity=95, entity_overlap=0, min_entities=0)
        assert result == "LOW"

    def test_exact_high_threshold(self):
        # similarity=90, overlap_ratio=0.60
        result = classify_confidence(similarity=90, entity_overlap=3, min_entities=5)
        assert result == "HIGH"

    def test_exact_medium_threshold(self):
        # similarity=80, overlap_ratio=0.40
        result = classify_confidence(similarity=80, entity_overlap=2, min_entities=5)
        assert result == "MEDIUM"


# ---------------------------------------------------------------------------
# match_markets_to_events
# ---------------------------------------------------------------------------

class TestMatchMarketsToEvents:
    def test_basic_match(self):
        pm_markets = [
            {"question": "Will Bitcoin reach $100,000 by end of 2025?", "conditionId": "pm1"},
        ]
        kalshi_events = [
            {"title": "Bitcoin to reach $100,000 by end of 2025", "event_ticker": "BTC-100K"},
        ]
        matches = match_markets_to_events(pm_markets, kalshi_events, threshold=70)
        assert len(matches) >= 1
        assert matches[0]["kalshi_event"]["event_ticker"] == "BTC-100K"

    def test_no_match_below_threshold(self):
        pm_markets = [
            {"question": "Will it rain tomorrow in NYC?", "conditionId": "pm1"},
        ]
        kalshi_events = [
            {"title": "Federal Reserve interest rate decision March 2025", "event_ticker": "FED-RATE"},
        ]
        matches = match_markets_to_events(pm_markets, kalshi_events, threshold=80)
        assert len(matches) == 0

    def test_confidence_filtering(self):
        pm_markets = [
            {"question": "Will Bitcoin reach $100,000 by end of 2025?", "conditionId": "pm1"},
        ]
        kalshi_events = [
            {"title": "Bitcoin to reach $100,000 by end of 2025", "event_ticker": "BTC-100K"},
        ]
        # With min_confidence=HIGH, only very strong matches should pass
        matches_high = match_markets_to_events(
            pm_markets, kalshi_events, threshold=70, min_confidence="HIGH"
        )
        matches_low = match_markets_to_events(
            pm_markets, kalshi_events, threshold=70, min_confidence="LOW"
        )
        assert len(matches_low) >= len(matches_high)

    def test_deduplication_by_kalshi_event(self):
        pm_markets = [
            {"question": "Will Bitcoin hit $100k?", "conditionId": "pm1"},
            {"question": "Bitcoin price above $100,000?", "conditionId": "pm2"},
        ]
        kalshi_events = [
            {"title": "Bitcoin to reach $100,000", "event_ticker": "BTC-100K"},
        ]
        matches = match_markets_to_events(pm_markets, kalshi_events, threshold=70)
        # Should only keep the best PM match per Kalshi event
        kalshi_tickers = [m["kalshi_event"]["event_ticker"] for m in matches]
        assert len(set(kalshi_tickers)) == len(kalshi_tickers)

    def test_short_title_filtered(self):
        pm_markets = [
            {"question": "Short?", "conditionId": "pm1"},
        ]
        kalshi_events = [
            {"title": "Short?", "event_ticker": "SHORT"},
        ]
        matches = match_markets_to_events(pm_markets, kalshi_events, threshold=50)
        assert len(matches) == 0

    def test_empty_inputs(self):
        assert match_markets_to_events([], []) == []
        assert match_markets_to_events([{"question": "test"}], []) == []
        assert match_markets_to_events([], [{"title": "test"}]) == []


# ---------------------------------------------------------------------------
# match_cross_platform
# ---------------------------------------------------------------------------

class TestMatchCrossPlatform:
    def test_basic_cross_platform_match(self):
        markets_a = [
            {"question": "Will inflation exceed 5% in 2025?", "conditionId": "a1"},
        ]
        markets_b = [
            {"title": "Inflation to exceed 5% in 2025", "ticker": "INF-5"},
        ]
        matches = match_cross_platform(
            markets_a, markets_b, "polymarket", "predictit", threshold=70
        )
        assert len(matches) >= 1
        assert matches[0]["platform_a"] == "polymarket"
        assert matches[0]["platform_b"] == "predictit"

    def test_no_match_different_topics(self):
        markets_a = [
            {"question": "Will Democrats win the Senate in 2026?", "conditionId": "a1"},
        ]
        markets_b = [
            {"title": "Bitcoin to reach $200,000 by 2027", "ticker": "BTC-200K"},
        ]
        matches = match_cross_platform(
            markets_a, markets_b, "polymarket", "betfair", threshold=80
        )
        assert len(matches) == 0

    def test_deduplication_by_platform_b(self):
        markets_a = [
            {"question": "Bitcoin price above 100k?", "conditionId": "a1"},
            {"question": "Will BTC exceed $100,000?", "conditionId": "a2"},
        ]
        markets_b = [
            {"title": "Bitcoin to reach $100,000", "ticker": "BTC-100K"},
        ]
        matches = match_cross_platform(
            markets_a, markets_b, "polymarket", "manifold", threshold=70
        )
        # Should deduplicate by platform B market
        b_ids = [m["market_b"].get("ticker") for m in matches]
        assert len(set(b_ids)) == len(b_ids)

    def test_uses_various_title_fields(self):
        # The matcher should use question, title, name, or shortName
        markets_a = [
            {"name": "Federal Reserve rate cut probability March 2025", "id": "a1"},
        ]
        markets_b = [
            {"shortName": "Fed rate cut March 2025 probability", "slug": "fed-rate"},
        ]
        matches = match_cross_platform(
            markets_a, markets_b, "platform_a", "platform_b", threshold=70
        )
        # Should find a match using name/shortName
        assert len(matches) >= 1


# ---------------------------------------------------------------------------
# detect_inverted
# ---------------------------------------------------------------------------

class TestDetectInverted:
    def test_not_inverted(self):
        assert detect_inverted(
            "Will Bitcoin hit $100k?",
            "Bitcoin to reach $100,000"
        ) is False

    def test_inverted_with_not(self):
        assert detect_inverted(
            "Will X happen?",
            "X will not happen"
        ) is True

    def test_inverted_with_wont(self):
        assert detect_inverted(
            "Will market go up?",
            "Market won't go up"
        ) is True

    def test_inverted_with_below(self):
        assert detect_inverted(
            "Bitcoin above $50k?",
            "Bitcoin below $50k"
        ) is True

    def test_inverted_with_under(self):
        assert detect_inverted(
            "Unemployment rate over 5%?",
            "Unemployment rate under 5%"
        ) is True

    def test_inverted_with_less_than(self):
        assert detect_inverted(
            "GDP growth more than 3%?",
            "GDP growth less than 3%"
        ) is True

    def test_inverted_with_fewer(self):
        assert detect_inverted(
            "More than 100 votes?",
            "Fewer than 100 votes"
        ) is True

    def test_not_as_substring_not_detected(self):
        # "notable" contains "not" but shouldn't trigger inversion
        # The implementation uses " not " with spaces, so this should be fine
        assert detect_inverted(
            "Will something happen?",
            "A notable event occurs"
        ) is False

    def test_case_insensitive(self):
        assert detect_inverted(
            "Will X happen?",
            "X Will NOT Happen"
        ) is True


# ---------------------------------------------------------------------------
# partial_ratio fallback
# ---------------------------------------------------------------------------

class TestPartialRatioFallback:
    def test_partial_ratio_catches_borderline_match_events(self):
        """partial_ratio fallback should catch markets with same subject but different phrasing."""
        pm_markets = [
            {"question": "Will Trump win the 2028 presidential election?", "conditionId": "pm1"},
        ]
        kalshi_events = [
            {"title": "Trump wins the 2028 election", "event_ticker": "TRUMP-2028"},
        ]
        # With a lower threshold, the partial_ratio fallback should help
        matches = match_markets_to_events(pm_markets, kalshi_events, threshold=72)
        assert len(matches) >= 1

    def test_partial_ratio_catches_borderline_cross_platform(self):
        """partial_ratio fallback works for cross-platform matching too."""
        markets_a = [
            {"question": "Will the unemployment rate exceed 6% in 2026?", "conditionId": "a1"},
        ]
        markets_b = [
            {"title": "Unemployment rate above 6% 2026", "ticker": "UNEMP-6"},
        ]
        matches = match_cross_platform(
            markets_a, markets_b, "polymarket", "predictit", threshold=72
        )
        assert len(matches) >= 1

    def test_threshold_sourced_from_config(self):
        """The default threshold from config should be 72."""
        from config import FUZZY_MATCH_THRESHOLD
        assert FUZZY_MATCH_THRESHOLD == 72

    def test_lowered_threshold_catches_more_matches(self):
        """Lower threshold (72) should find more matches than higher (80)."""
        pm_markets = [
            {"question": "Will Bitcoin price be above $150,000 by December 2026?", "conditionId": "pm1"},
            {"question": "Will the Federal Reserve cut rates in March 2026?", "conditionId": "pm2"},
        ]
        kalshi_events = [
            {"title": "Bitcoin to reach $150,000 by end of 2026", "event_ticker": "BTC-150K"},
            {"title": "Fed rate cut March 2026", "event_ticker": "FED-MAR"},
        ]
        matches_72 = match_markets_to_events(pm_markets, kalshi_events, threshold=72)
        matches_85 = match_markets_to_events(pm_markets, kalshi_events, threshold=85)
        assert len(matches_72) >= len(matches_85)
