"""Tests for the first-class Stage 2 refiner in scans/news_snipe.py.

Focused on the new behaviour added in PR B:
- Headline freshness gate (NEWS_SNIPE_MAX_AGE_MINUTES)
- Live CLOB ask check — drop if the market has already moved past the
  sentiment direction
- _news_timestamp captured during Stage 1

Existing confidence-floor + cooldown coverage lives in tests/test_news_snipe.py.
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Hold a stable module reference and use ``patch.object(ns_mod, ...)`` —
# see tests/test_time_decay_refiner.py for the full rationale. Other
# test files may pop ``scans.news_snipe`` from sys.modules between
# tests; ``patch("scans.news_snipe...")`` would then target a freshly
# re-imported module dict, while the function we call here still
# references the original dict, and the mock would never be seen.
import scans.news_snipe as ns_mod

_refine_news_with_confidence = ns_mod._refine_news_with_confidence
extract_news_signals = ns_mod.extract_news_signals


def _make_signal(market_key="m1", sentiment="YES", confidence=0.8,
                 news_ts=None):
    return {
        "type": "NewsSnipe",
        "market": "Will X happen?",
        "_headline": "X approved by regulators",
        "_sentiment": sentiment,
        "_confidence": confidence,
        "_market_key": market_key,
        "_news_timestamp": news_ts,
    }


def _make_market(market_key="m1"):
    return {
        "question": f"Will {market_key} happen?",
        "clobTokenIds": '["yes_tok_1", "no_tok_1"]',
    }


# ---------------------------------------------------------------------------
# TestStage1TimestampCapture
# ---------------------------------------------------------------------------


class TestStage1TimestampCapture:
    """extract_news_signals() must store headline.datetime as _news_timestamp."""

    def test_signal_includes_news_timestamp(self):
        headlines = [{
            "headline": "X approved by regulators",
            "summary": "Regulator confirms approval",
            "datetime": 1700000000,
        }]
        markets = {"m1": {"question": "Will X be approved?"}}

        signals = extract_news_signals(headlines, markets, fuzzy_threshold=20)

        assert len(signals) >= 1
        assert signals[0]["_news_timestamp"] == 1700000000

    def test_signal_handles_missing_datetime(self):
        headlines = [{
            "headline": "X approved by regulators",
            "summary": "",
        }]
        markets = {"m1": {"question": "Will X be approved?"}}

        signals = extract_news_signals(headlines, markets, fuzzy_threshold=20)

        assert len(signals) >= 1
        # Missing datetime → None, refiner treats as fresh.
        assert signals[0]["_news_timestamp"] is None


# ---------------------------------------------------------------------------
# TestFreshnessGate
# ---------------------------------------------------------------------------


class TestFreshnessGate:
    """Refiner drops signals older than NEWS_SNIPE_MAX_AGE_MINUTES."""

    def test_drops_stale_headline(self):
        now = 1_700_000_000.0
        # Headline is 2 hours old, default cap is 60 min.
        opp = _make_signal(news_ts=now - 7200)

        refined = _refine_news_with_confidence(
            [opp],
            confidence_floor=0.5,
            current_time=now,
            max_age_minutes=60,
        )

        assert refined == []

    def test_keeps_fresh_headline(self):
        now = 1_700_000_000.0
        # Headline is 30 minutes old, well under the 60 min cap.
        opp = _make_signal(news_ts=now - 1800)

        refined = _refine_news_with_confidence(
            [opp],
            confidence_floor=0.5,
            current_time=now,
            max_age_minutes=60,
        )

        assert len(refined) == 1

    def test_missing_timestamp_passes_freshness(self):
        """No _news_timestamp → can't decide age, don't penalize."""
        opp = _make_signal(news_ts=None)

        refined = _refine_news_with_confidence(
            [opp],
            confidence_floor=0.5,
            max_age_minutes=60,
        )

        assert len(refined) == 1

    def test_custom_max_age_override_takes_precedence(self):
        """Caller-supplied max_age_minutes wins over the env default."""
        now = 1_700_000_000.0
        opp = _make_signal(news_ts=now - 600)  # 10 min old

        # 5 minute cap → should drop
        assert _refine_news_with_confidence(
            [opp], current_time=now, max_age_minutes=5,
        ) == []

        # 60 minute cap → should keep
        assert len(_refine_news_with_confidence(
            [opp], current_time=now, max_age_minutes=60,
        )) == 1


# ---------------------------------------------------------------------------
# TestCLOBPriceCheck
# ---------------------------------------------------------------------------


class TestCLOBPriceCheck:
    """When markets_by_key is supplied, refiner re-fetches CLOB ask
    and drops signals where the market already crossed the threshold."""

    def test_drops_yes_sentiment_when_yes_ask_already_high(self):
        opp = _make_signal(sentiment="YES")
        markets = {"m1": _make_market("m1")}
        clob = {"yes_ask": 0.55, "no_ask": 0.45,
                "yes_ask_size": 100, "no_ask_size": 100}

        with patch.object(ns_mod, "_fetch_clob_for_market",
                   return_value=(markets["m1"], clob)):
            refined = _refine_news_with_confidence(
                [opp], markets_by_key=markets, max_age_minutes=60,
            )

        assert refined == []

    def test_keeps_yes_sentiment_when_yes_ask_still_low(self):
        opp = _make_signal(sentiment="YES")
        markets = {"m1": _make_market("m1")}
        clob = {"yes_ask": 0.40, "no_ask": 0.60,
                "yes_ask_size": 50, "no_ask_size": 50}

        with patch.object(ns_mod, "_fetch_clob_for_market",
                   return_value=(markets["m1"], clob)):
            refined = _refine_news_with_confidence(
                [opp], markets_by_key=markets, max_age_minutes=60,
            )

        assert len(refined) == 1
        assert refined[0]["_clob_yes_ask"] == 0.40
        assert refined[0]["_clob_no_ask"] == 0.60

    def test_drops_no_sentiment_when_no_ask_already_high(self):
        opp = _make_signal(sentiment="NO")
        markets = {"m1": _make_market("m1")}
        clob = {"yes_ask": 0.40, "no_ask": 0.60,
                "yes_ask_size": 50, "no_ask_size": 50}

        with patch.object(ns_mod, "_fetch_clob_for_market",
                   return_value=(markets["m1"], clob)):
            refined = _refine_news_with_confidence(
                [opp], markets_by_key=markets, max_age_minutes=60,
            )

        assert refined == []

    def test_clob_fetch_failure_does_not_drop(self):
        opp = _make_signal(sentiment="YES")
        markets = {"m1": _make_market("m1")}

        with patch.object(ns_mod, "_fetch_clob_for_market",
                   side_effect=RuntimeError("CLOB down")):
            refined = _refine_news_with_confidence(
                [opp], markets_by_key=markets, max_age_minutes=60,
            )

        # CLOB unavailable → fall through to other gates, opp passes.
        assert len(refined) == 1


# ---------------------------------------------------------------------------
# TestBackwardCompat
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    """Existing call sites with no markets_by_key still work."""

    def test_no_markets_no_clob_check(self):
        opp = _make_signal(sentiment="YES", news_ts=None)
        refined = _refine_news_with_confidence(
            [opp], confidence_floor=0.5, max_age_minutes=60,
        )
        assert len(refined) == 1

    def test_low_confidence_still_drops(self):
        opp = _make_signal(confidence=0.3)
        refined = _refine_news_with_confidence(
            [opp], confidence_floor=0.5, max_age_minutes=60,
        )
        assert refined == []

    def test_cooldown_still_drops(self):
        now = 1_700_000_000.0
        opp = _make_signal(news_ts=now - 60)  # fresh
        cooldown = {"m1": now + 30}  # cooldown active
        refined = _refine_news_with_confidence(
            [opp],
            cooldown_cache=cooldown,
            current_time=now,
            max_age_minutes=60,
        )
        assert refined == []

    def test_empty_input_returns_empty(self):
        assert _refine_news_with_confidence([]) == []


# ---------------------------------------------------------------------------
# TestEnvDefault
# ---------------------------------------------------------------------------


class TestEnvDefault:
    """When max_age_minutes is None, refiner reads NEWS_SNIPE_MAX_AGE_MINUTES."""

    def test_env_var_used_when_not_overridden(self):
        # Patch the config import inside the function. The refiner does a
        # local `from config import NEWS_SNIPE_MAX_AGE_MINUTES` so we patch
        # the attribute on the already-loaded config module.
        import config
        with patch.object(config, "NEWS_SNIPE_MAX_AGE_MINUTES", 5):
            now = 1_700_000_000.0
            opp = _make_signal(news_ts=now - 600)  # 10 min old > 5 min cap
            refined = _refine_news_with_confidence(
                [opp], current_time=now,  # max_age_minutes deliberately omitted
            )
        assert refined == []
