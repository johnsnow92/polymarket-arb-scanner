"""Tests for event_monitor.py — Metaculus divergence signal detection."""

import sys
import os
import time
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from event_monitor import EventMonitor
from metaculus_api import MetaculusClient


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _make_metaculus_question(
    qid: int,
    title: str,
    prob: float,
    forecasters: int = 50,
) -> dict:
    """Build a fake Metaculus question dict."""
    return {
        "id": qid,
        "title": title,
        "number_of_forecasters": forecasters,
        "community_prediction": {
            "full": {"q1": prob - 0.1, "q2": prob, "q3": prob + 0.1}
        },
    }


def _make_polymarket_market(title: str, yes_price: float) -> dict:
    """Build a fake Polymarket market dict."""
    no_price = round(1.0 - yes_price, 4)
    return {
        "question": title,
        "conditionId": f"pm-{hash(title) % 10000}",
        "outcomePrices": f'["{yes_price}", "{no_price}"]',
    }


def _make_kalshi_market(title: str, yes_cents: int) -> dict:
    """Build a fake Kalshi market dict."""
    return {
        "title": title,
        "ticker": f"K-{hash(title) % 10000}",
        "yes_price": yes_cents,
    }


@pytest.fixture
def mock_client():
    """Create a mocked MetaculusClient."""
    client = MagicMock(spec=MetaculusClient)
    return client


@pytest.fixture
def monitor(mock_client):
    """Create an EventMonitor with a mocked MetaculusClient."""
    return EventMonitor(
        metaculus_client=mock_client,
        divergence_threshold=0.10,
        min_metaculus_forecasters=20,
        match_threshold=72,
    )


# ---------------------------------------------------------------------------
# _match_market_to_question
# ---------------------------------------------------------------------------

class TestMatchMarketToQuestion:
    def test_finds_correct_match_with_high_similarity(self, monitor):
        """Should match a market to the most similar Metaculus question."""
        questions = [
            _make_metaculus_question(1, "Will Bitcoin reach $100,000 by end of 2026?", 0.65),
            _make_metaculus_question(2, "Will the Federal Reserve cut rates in March 2026?", 0.40),
        ]
        result = monitor._match_market_to_question(
            "Will Bitcoin reach $100,000 by end of 2026?", questions
        )
        assert result is not None
        assert result["id"] == 1

    def test_returns_none_when_no_match_found(self, monitor):
        """Should return None when no question matches above threshold."""
        questions = [
            _make_metaculus_question(1, "Will Mars be colonized by 2050?", 0.10),
            _make_metaculus_question(2, "Will cold fusion be achieved?", 0.05),
        ]
        result = monitor._match_market_to_question(
            "Will the Yankees win the 2026 World Series?", questions
        )
        assert result is None

    def test_returns_none_for_short_title(self, monitor):
        """Short titles (< 8 chars normalized) should return None."""
        questions = [_make_metaculus_question(1, "Short", 0.5)]
        result = monitor._match_market_to_question("Short?", questions)
        assert result is None

    def test_returns_none_for_empty_questions(self, monitor):
        """Empty questions list should return None."""
        result = monitor._match_market_to_question("Some market title here", [])
        assert result is None


# ---------------------------------------------------------------------------
# _get_platform_yes_price
# ---------------------------------------------------------------------------

class TestGetPlatformYesPrice:
    def test_extracts_polymarket_price_from_outcome_prices_string(self, monitor):
        """Should parse JSON string outcomePrices for Polymarket."""
        market = {"outcomePrices": '["0.65", "0.35"]'}
        price = monitor._get_platform_yes_price(market, "polymarket")
        assert price == pytest.approx(0.65)

    def test_extracts_polymarket_price_from_outcome_prices_list(self, monitor):
        """Should parse list outcomePrices for Polymarket."""
        market = {"outcomePrices": [0.72, 0.28]}
        price = monitor._get_platform_yes_price(market, "polymarket")
        assert price == pytest.approx(0.72)

    def test_extracts_polymarket_price_from_tokens_array(self, monitor):
        """Should fall back to tokens array when outcomePrices missing."""
        market = {"tokens": [{"price": 0.55}, {"price": 0.45}]}
        price = monitor._get_platform_yes_price(market, "polymarket")
        assert price == pytest.approx(0.55)

    def test_extracts_kalshi_price_from_yes_price(self, monitor):
        """Should extract Kalshi YES price (in cents) and convert to 0-1."""
        market = {"yes_price": 65}
        price = monitor._get_platform_yes_price(market, "kalshi")
        assert price == pytest.approx(0.65)

    def test_extracts_kalshi_price_from_last_price(self, monitor):
        """Should fall back to last_price for Kalshi."""
        market = {"last_price": 42}
        price = monitor._get_platform_yes_price(market, "kalshi")
        assert price == pytest.approx(0.42)

    def test_returns_none_for_unsupported_platform(self, monitor):
        """Should return None for platforms like betfair, smarkets, etc."""
        market = {"some_field": 0.5}
        assert monitor._get_platform_yes_price(market, "betfair") is None
        assert monitor._get_platform_yes_price(market, "smarkets") is None
        assert monitor._get_platform_yes_price(market, "sxbet") is None
        assert monitor._get_platform_yes_price(market, "matchbook") is None

    def test_returns_none_when_no_price_data(self, monitor):
        """Should return None when market dict has no parseable price."""
        market = {"question": "Some market"}
        assert monitor._get_platform_yes_price(market, "polymarket") is None
        assert monitor._get_platform_yes_price(market, "kalshi") is None


# ---------------------------------------------------------------------------
# find_divergences
# ---------------------------------------------------------------------------

class TestFindDivergences:
    def test_detects_divergence_above_threshold(self, monitor, mock_client):
        """Should detect a 15% divergence (platform=0.50, metaculus=0.65)."""
        questions = [
            _make_metaculus_question(
                1, "Will Bitcoin reach $100,000 by end of 2026?", 0.65, forecasters=100
            ),
        ]
        mock_client.fetch_active_questions.return_value = questions

        markets = [
            _make_polymarket_market("Will Bitcoin reach $100,000 by end of 2026?", 0.50),
        ]
        divergences = monitor.find_divergences(markets, "polymarket")

        assert len(divergences) == 1
        assert divergences[0]["divergence"] == pytest.approx(0.15)
        assert divergences[0]["platform_price"] == pytest.approx(0.50)
        assert divergences[0]["metaculus_prob"] == pytest.approx(0.65)
        assert divergences[0]["direction"] == "BUY_YES"

    def test_ignores_small_divergences_below_threshold(self, monitor, mock_client):
        """Should ignore divergences below the 10% threshold."""
        questions = [
            _make_metaculus_question(
                1, "Will Bitcoin reach $100,000 by end of 2026?", 0.55, forecasters=100
            ),
        ]
        mock_client.fetch_active_questions.return_value = questions

        markets = [
            _make_polymarket_market("Will Bitcoin reach $100,000 by end of 2026?", 0.50),
        ]
        divergences = monitor.find_divergences(markets, "polymarket")

        # 5% divergence is below 10% threshold
        assert len(divergences) == 0

    def test_skips_questions_with_few_forecasters(self, monitor, mock_client):
        """Should skip questions with fewer than min_metaculus_forecasters."""
        questions = [
            _make_metaculus_question(
                1, "Will Bitcoin reach $100,000 by end of 2026?", 0.80, forecasters=5
            ),
        ]
        mock_client.fetch_active_questions.return_value = questions

        markets = [
            _make_polymarket_market("Will Bitcoin reach $100,000 by end of 2026?", 0.50),
        ]
        divergences = monitor.find_divergences(markets, "polymarket")

        # 30% divergence but only 5 forecasters (below min of 20)
        assert len(divergences) == 0

    def test_returns_empty_when_no_questions(self, monitor, mock_client):
        """Should return empty list when Metaculus has no questions."""
        mock_client.fetch_active_questions.return_value = []

        markets = [_make_polymarket_market("Some market", 0.50)]
        divergences = monitor.find_divergences(markets, "polymarket")

        assert divergences == []

    def test_buy_no_direction_when_metaculus_lower(self, monitor, mock_client):
        """Direction should be BUY_NO when metaculus < platform."""
        questions = [
            _make_metaculus_question(
                1, "Will Bitcoin reach $100,000 by end of 2026?", 0.35, forecasters=100
            ),
        ]
        mock_client.fetch_active_questions.return_value = questions

        markets = [
            _make_polymarket_market("Will Bitcoin reach $100,000 by end of 2026?", 0.50),
        ]
        divergences = monitor.find_divergences(markets, "polymarket")

        assert len(divergences) == 1
        assert divergences[0]["direction"] == "BUY_NO"


# ---------------------------------------------------------------------------
# build_signal_opportunities
# ---------------------------------------------------------------------------

class TestBuildSignalOpportunities:
    def test_creates_proper_opportunity_dict(self, monitor):
        """Should produce an opportunity dict with all standard keys."""
        divergences = [{
            "market_title": "Will Bitcoin reach $100,000 by end of 2026?",
            "platform_price": 0.50,
            "metaculus_prob": 0.65,
            "divergence": 0.15,
            "question_id": 42,
            "platform_name": "polymarket",
            "direction": "BUY_YES",
            "num_forecasters": 100,
            "market": {},
        }]
        opps = monitor.build_signal_opportunities(divergences)

        assert len(opps) == 1
        opp = opps[0]
        assert opp["type"] == "EventDivergence"
        assert "Bitcoin" in opp["market"]
        assert opp["_platform"] == "polymarket"
        assert opp["_metaculus_id"] == 42
        assert opp["_metaculus_prob"] == pytest.approx(0.65)
        assert opp["_divergence"] == pytest.approx(0.15)
        assert opp["_direction"] == "BUY_YES"
        assert opp["_clob_depth"] == 0
        assert opp["net_profit"] == pytest.approx(0.075)  # 0.15 * 0.5

    def test_high_confidence_for_large_divergence(self, monitor):
        """Divergence >= 0.20 should be HIGH confidence."""
        divergences = [{
            "market_title": "Some market with large divergence",
            "platform_price": 0.40,
            "metaculus_prob": 0.65,
            "divergence": 0.25,
            "question_id": 1,
            "platform_name": "polymarket",
            "direction": "BUY_YES",
            "num_forecasters": 100,
            "market": {},
        }]
        opps = monitor.build_signal_opportunities(divergences)
        assert opps[0]["confidence"] == "HIGH"

    def test_medium_confidence_for_moderate_divergence(self, monitor):
        """Divergence >= 0.15 but < 0.20 should be MEDIUM confidence."""
        divergences = [{
            "market_title": "Some market with moderate divergence",
            "platform_price": 0.50,
            "metaculus_prob": 0.67,
            "divergence": 0.17,
            "question_id": 2,
            "platform_name": "polymarket",
            "direction": "BUY_YES",
            "num_forecasters": 100,
            "market": {},
        }]
        opps = monitor.build_signal_opportunities(divergences)
        assert opps[0]["confidence"] == "MEDIUM"

    def test_low_confidence_for_small_divergence(self, monitor):
        """Divergence >= threshold but < 0.15 should be LOW confidence."""
        divergences = [{
            "market_title": "Some market with small divergence",
            "platform_price": 0.50,
            "metaculus_prob": 0.61,
            "divergence": 0.11,
            "question_id": 3,
            "platform_name": "polymarket",
            "direction": "BUY_YES",
            "num_forecasters": 100,
            "market": {},
        }]
        opps = monitor.build_signal_opportunities(divergences)
        assert opps[0]["confidence"] == "LOW"

    def test_net_roi_calculation(self, monitor):
        """Net ROI should be (divergence * 0.5 / platform_price * 100)%."""
        divergences = [{
            "market_title": "Market for ROI testing with entities",
            "platform_price": 0.40,
            "metaculus_prob": 0.60,
            "divergence": 0.20,
            "question_id": 4,
            "platform_name": "kalshi",
            "direction": "BUY_YES",
            "num_forecasters": 50,
            "market": {},
        }]
        opps = monitor.build_signal_opportunities(divergences)
        # net_profit = 0.20 * 0.5 = 0.10
        # net_roi = (0.10 / 0.40) * 100 = 25.00%
        assert opps[0]["net_roi"] == "25.00%"

    def test_zero_platform_price_roi(self, monitor):
        """Should handle zero platform price without division error."""
        divergences = [{
            "market_title": "Zero price edge case for testing",
            "platform_price": 0.0,
            "metaculus_prob": 0.50,
            "divergence": 0.50,
            "question_id": 5,
            "platform_name": "polymarket",
            "direction": "BUY_YES",
            "num_forecasters": 100,
            "market": {},
        }]
        opps = monitor.build_signal_opportunities(divergences)
        assert opps[0]["net_roi"] == "0%"


# ---------------------------------------------------------------------------
# scan_event_divergences (full pipeline)
# ---------------------------------------------------------------------------

class TestScanEventDivergences:
    def test_full_pipeline_with_mocked_metaculus(self, monitor, mock_client):
        """End-to-end scan should find divergences across platforms."""
        questions = [
            _make_metaculus_question(
                1, "Will Bitcoin reach $100,000 by end of 2026?", 0.75, forecasters=200
            ),
            _make_metaculus_question(
                2, "Will the Federal Reserve cut rates in March 2026?", 0.40, forecasters=150
            ),
        ]
        mock_client.fetch_active_questions.return_value = questions

        platform_markets = {
            "polymarket": [
                _make_polymarket_market(
                    "Will Bitcoin reach $100,000 by end of 2026?", 0.55
                ),
            ],
            "kalshi": [
                _make_kalshi_market(
                    "Will the Federal Reserve cut rates in March 2026?", 25
                ),
            ],
        }

        results = monitor.scan_event_divergences(platform_markets, min_profit=0.005)

        assert len(results) >= 1
        # All results should have positive profit above threshold
        for opp in results:
            assert opp["net_profit"] >= 0.005
            assert opp["type"] == "EventDivergence"

    def test_returns_empty_when_no_divergences(self, monitor, mock_client):
        """Should return empty list when all prices align with Metaculus."""
        questions = [
            _make_metaculus_question(
                1, "Will Bitcoin reach $100,000 by end of 2026?", 0.55, forecasters=100
            ),
        ]
        mock_client.fetch_active_questions.return_value = questions

        platform_markets = {
            "polymarket": [
                _make_polymarket_market(
                    "Will Bitcoin reach $100,000 by end of 2026?", 0.55
                ),
            ],
        }

        results = monitor.scan_event_divergences(platform_markets)

        # 0% divergence, nothing should be returned
        assert results == []

    def test_filters_by_min_profit(self, monitor, mock_client):
        """Should filter out opportunities below min_profit threshold."""
        questions = [
            _make_metaculus_question(
                1, "Will Bitcoin reach $100,000 by end of 2026?", 0.61, forecasters=100
            ),
        ]
        mock_client.fetch_active_questions.return_value = questions

        platform_markets = {
            "polymarket": [
                _make_polymarket_market(
                    "Will Bitcoin reach $100,000 by end of 2026?", 0.50
                ),
            ],
        }

        # divergence = 0.11, net_profit = 0.055
        # With high min_profit, should be filtered out
        results = monitor.scan_event_divergences(platform_markets, min_profit=0.10)
        assert results == []

        # With lower min_profit, should pass
        results = monitor.scan_event_divergences(platform_markets, min_profit=0.01)
        assert len(results) == 1

    def test_results_sorted_by_profit_descending(self, monitor, mock_client):
        """Results should be sorted with highest profit first."""
        questions = [
            _make_metaculus_question(
                1, "Will Bitcoin reach $100,000 by end of 2026?", 0.80, forecasters=100
            ),
            _make_metaculus_question(
                2, "Will the Federal Reserve cut rates in March 2026?", 0.75, forecasters=100
            ),
        ]
        mock_client.fetch_active_questions.return_value = questions

        platform_markets = {
            "polymarket": [
                _make_polymarket_market(
                    "Will Bitcoin reach $100,000 by end of 2026?", 0.50
                ),
                _make_polymarket_market(
                    "Will the Federal Reserve cut rates in March 2026?", 0.55
                ),
            ],
        }

        results = monitor.scan_event_divergences(platform_markets, min_profit=0.005)

        if len(results) >= 2:
            assert results[0]["net_profit"] >= results[1]["net_profit"]

    def test_empty_platform_markets(self, monitor, mock_client):
        """Should handle empty platform markets dict gracefully."""
        results = monitor.scan_event_divergences({})
        assert results == []

    def test_skips_empty_market_lists(self, monitor, mock_client):
        """Should skip platforms with empty market lists."""
        mock_client.fetch_active_questions.return_value = []
        results = monitor.scan_event_divergences({"polymarket": []})
        assert results == []


# ---------------------------------------------------------------------------
# Question caching
# ---------------------------------------------------------------------------

class TestQuestionCaching:
    def test_cache_avoids_refetch(self, monitor, mock_client):
        """Second call within TTL should use cached questions, not re-fetch."""
        questions = [
            _make_metaculus_question(
                1, "Will Bitcoin reach $100,000 by end of 2026?", 0.75, forecasters=100
            ),
        ]
        mock_client.fetch_active_questions.return_value = questions

        markets = [
            _make_polymarket_market(
                "Will Bitcoin reach $100,000 by end of 2026?", 0.50
            ),
        ]

        # First call should fetch
        monitor.find_divergences(markets, "polymarket")
        assert mock_client.fetch_active_questions.call_count == 1

        # Second call should use cache
        monitor.find_divergences(markets, "polymarket")
        assert mock_client.fetch_active_questions.call_count == 1

    def test_cache_expires_after_ttl(self, monitor, mock_client):
        """Cache should expire and re-fetch after TTL elapses."""
        questions = [
            _make_metaculus_question(
                1, "Will Bitcoin reach $100,000 by end of 2026?", 0.75, forecasters=100
            ),
        ]
        mock_client.fetch_active_questions.return_value = questions

        markets = [
            _make_polymarket_market(
                "Will Bitcoin reach $100,000 by end of 2026?", 0.50
            ),
        ]

        # First call
        monitor.find_divergences(markets, "polymarket")
        assert mock_client.fetch_active_questions.call_count == 1

        # Simulate cache expiry
        monitor._questions_cache_ts = time.time() - 400  # Beyond 300s TTL

        # Third call should re-fetch
        monitor.find_divergences(markets, "polymarket")
        assert mock_client.fetch_active_questions.call_count == 2


# ---------------------------------------------------------------------------
# Direction logic
# ---------------------------------------------------------------------------

class TestDirectionLogic:
    def test_buy_yes_when_metaculus_higher(self, monitor, mock_client):
        """Direction is BUY_YES when metaculus probability > platform price."""
        questions = [
            _make_metaculus_question(
                1, "Will Bitcoin reach $100,000 by end of 2026?", 0.80, forecasters=100
            ),
        ]
        mock_client.fetch_active_questions.return_value = questions

        markets = [
            _make_polymarket_market(
                "Will Bitcoin reach $100,000 by end of 2026?", 0.55
            ),
        ]
        divergences = monitor.find_divergences(markets, "polymarket")

        assert len(divergences) == 1
        assert divergences[0]["direction"] == "BUY_YES"

    def test_buy_no_when_metaculus_lower(self, monitor, mock_client):
        """Direction is BUY_NO when metaculus probability < platform price."""
        questions = [
            _make_metaculus_question(
                1, "Will Bitcoin reach $100,000 by end of 2026?", 0.35, forecasters=100
            ),
        ]
        mock_client.fetch_active_questions.return_value = questions

        markets = [
            _make_polymarket_market(
                "Will Bitcoin reach $100,000 by end of 2026?", 0.55
            ),
        ]
        divergences = monitor.find_divergences(markets, "polymarket")

        assert len(divergences) == 1
        assert divergences[0]["direction"] == "BUY_NO"
