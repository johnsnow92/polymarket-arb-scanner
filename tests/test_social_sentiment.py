"""Tests for scans/social_sentiment.py — Strategy #39 Social Sentiment."""

import pytest
from unittest.mock import patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock py_clob_client before importing scan modules
sys.modules["py_clob_client"] = MagicMock()
sys.modules["py_clob_client.clob_types"] = MagicMock()
sys.modules["py_clob_client.client"] = MagicMock()

from scans.social_sentiment import scan_social_sentiment, _aggregate_sentiment


class TestAggregateSentiment:
    def test_empty_title_returns_none(self):
        result = _aggregate_sentiment(
            twitter_client=MagicMock(),
            reddit_client=MagicMock(),
            market_title="",
        )
        assert result is None

    def test_no_clients_returns_none(self):
        result = _aggregate_sentiment(
            twitter_client=None,
            reddit_client=None,
            market_title="Test market",
        )
        assert result is None

    def test_twitter_only_sentiment(self):
        twitter = MagicMock()
        twitter.get_market_sentiment.return_value = {
            "sentiment_score": 0.6,
            "sample_size": 50,
        }
        result = _aggregate_sentiment(
            twitter_client=twitter,
            reddit_client=None,
            market_title="Test market",
        )
        assert result is not None
        assert result["sample_size"] == 50
        assert result["implied_prob"] == pytest.approx(0.80, abs=0.01)

    def test_reddit_only_sentiment(self):
        reddit = MagicMock()
        reddit.get_market_sentiment.return_value = {
            "sentiment_score": -0.4,
            "sample_size": 20,
        }
        result = _aggregate_sentiment(
            twitter_client=None,
            reddit_client=reddit,
            market_title="Test market",
        )
        assert result is not None
        assert result["sample_size"] == 20
        assert result["implied_prob"] == pytest.approx(0.30, abs=0.01)

    def test_combined_sentiment_weighted(self):
        twitter = MagicMock()
        twitter.get_market_sentiment.return_value = {
            "sentiment_score": 0.5,
            "sample_size": 100,
        }
        reddit = MagicMock()
        reddit.get_market_sentiment.return_value = {
            "sentiment_score": 0.3,
            "sample_size": 30,
        }

        with patch("scans.social_sentiment.SOCIAL_SENTIMENT_WEIGHT_TWITTER", 0.6):
            with patch("scans.social_sentiment.SOCIAL_SENTIMENT_WEIGHT_REDDIT", 0.4):
                result = _aggregate_sentiment(
                    twitter_client=twitter,
                    reddit_client=reddit,
                    market_title="Test market",
                )
                assert result is not None
                assert result["sample_size"] == 130
                assert result["sources"] == 2

    def test_low_sample_size_excluded(self):
        twitter = MagicMock()
        twitter.get_market_sentiment.return_value = {
            "sentiment_score": 0.5,
            "sample_size": 2,
        }
        result = _aggregate_sentiment(
            twitter_client=twitter,
            reddit_client=None,
            market_title="Test market",
        )
        assert result is None


class TestScanSocialSentiment:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        with patch("scans.social_sentiment.SOCIAL_SENTIMENT_ENABLED", True):
            with patch("scans.social_sentiment.SOCIAL_SENTIMENT_MIN_DIVERGENCE", 0.10):
                with patch("scans.social_sentiment.SOCIAL_SENTIMENT_MIN_SAMPLE_SIZE", 10):
                    yield

    def test_disabled_returns_empty(self):
        with patch("scans.social_sentiment.SOCIAL_SENTIMENT_ENABLED", False):
            result = scan_social_sentiment([])
            assert result == []

    def test_no_clients_returns_empty(self):
        result = scan_social_sentiment([], twitter_client=None, reddit_client=None)
        assert result == []

    def test_empty_markets_returns_empty(self):
        twitter = MagicMock()
        result = scan_social_sentiment([], twitter_client=twitter)
        assert result == []

    def test_finds_divergence_opportunity(self):
        twitter = MagicMock()
        twitter.get_market_sentiment.return_value = {
            "sentiment_score": 0.6,
            "sample_size": 100,
        }

        markets = [
            {
                "title": "Will X happen?",
                "yes_price": 0.50,
                "condition_id": "m1",
            }
        ]

        with patch("fees.net_profit_social_sentiment") as mock_fee:
            mock_fee.return_value = {
                "net_profit": 0.20,
                "net_roi": 0.40,
            }
            result = scan_social_sentiment(
                markets,
                twitter_client=twitter,
                min_divergence=0.10,
                min_samples=10,
                min_profit=0.01,
            )

            assert len(result) > 0
            opp = result[0]
            assert opp["type"] == "SocialSentiment"
            assert opp["_layer"] == 4
            assert opp["_direction"] == "BUY_YES"

    def test_low_divergence_filtered(self):
        twitter = MagicMock()
        twitter.get_market_sentiment.return_value = {
            "sentiment_score": 0.1,
            "sample_size": 50,
        }

        markets = [
            {
                "title": "Test",
                "yes_price": 0.55,
                "condition_id": "m1",
            }
        ]

        result = scan_social_sentiment(
            markets,
            twitter_client=twitter,
            min_divergence=0.15,
        )
        assert result == []

    def test_low_sample_size_filtered(self):
        twitter = MagicMock()
        twitter.get_market_sentiment.return_value = {
            "sentiment_score": 0.8,
            "sample_size": 5,
        }

        markets = [
            {
                "title": "Test",
                "yes_price": 0.50,
                "condition_id": "m1",
            }
        ]

        result = scan_social_sentiment(
            markets,
            twitter_client=twitter,
            min_samples=20,
        )
        assert result == []


class TestSocialSentimentFeeFunction:
    def test_net_profit_social_sentiment(self):
        from fees import net_profit_social_sentiment
        result = net_profit_social_sentiment(
            market_price=0.50,
            implied_prob=0.70,
            platform="polymarket",
        )
        assert "net_profit" in result
        assert "gross_spread" in result
        assert result["gross_spread"] == pytest.approx(0.20, abs=0.01)
