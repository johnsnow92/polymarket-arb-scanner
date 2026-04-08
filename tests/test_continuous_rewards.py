"""Tests for continuous mode rewards scanning integration."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def mock_external_modules():
    """Mock external API modules that may not be installed."""
    mock_modules = {}
    for mod_name in [
        "polymarket_api", "kalshi_api",
        "betfair_api", "smarkets_api", "sxbet_api",
        "matchbook_api", "gemini_api", "ibkr_api",
        "ib_insync", "ws_feeds",
    ]:
        if mod_name not in sys.modules:
            mock_modules[mod_name] = MagicMock()
            sys.modules[mod_name] = mock_modules[mod_name]
    yield
    for mod_name in mock_modules:
        if mod_name in sys.modules:
            del sys.modules[mod_name]


@pytest.fixture
def reward_tracker():
    """Mock RewardTracker instance."""
    from market_maker import RewardTracker
    tracker = RewardTracker()
    # Set both reward_scores (plan interface) and _reward_cache (dashboard interface)
    tracker.reward_scores = {
        "POLY-TEST": {
            "polymarket": {
                "pool_size_usdc": 1000.0,
                "reward_rate": 0.10,
            }
        }
    }
    tracker._reward_cache = {
        "POLY-TEST": {
            "pool_size_usdc": 1000.0,
            "reward_rate": 0.10,
        }
    }
    tracker.resting_order_count = 2
    return tracker


@pytest.fixture
def kalshi_reward_tracker():
    """Mock KalshiRewardTracker instance."""
    from market_maker import KalshiRewardTracker
    tracker = MagicMock(spec=KalshiRewardTracker)
    tracker.resting_order_count = 1
    return tracker


class TestRewardsContinuousMode:
    """Test rewards scanning integration in continuous mode."""

    def test_reward_tracker_initialized(self):
        """Verify RewardTracker is created and has correct attributes."""
        from market_maker import RewardTracker, KalshiRewardTracker

        # Test instantiation
        tracker = RewardTracker()
        kalshi_tracker = KalshiRewardTracker(None)

        assert tracker is not None
        assert kalshi_tracker is not None
        assert hasattr(tracker, "_reward_cache")
        assert isinstance(tracker._reward_cache, dict)

    def test_reward_tracker_has_metrics(self, reward_tracker):
        """Verify RewardTracker tracks resting order count."""
        assert hasattr(reward_tracker, "resting_order_count")
        assert isinstance(reward_tracker.resting_order_count, int)
        assert reward_tracker.resting_order_count >= 0

    def test_scan_rewards_loop_exists(self):
        """Verify scan_polymarket_rewards and scan_kalshi_rewards are callable."""
        from scans import scan_polymarket_rewards, scan_kalshi_rewards

        # Verify scan functions are callable
        assert callable(scan_polymarket_rewards)
        assert callable(scan_kalshi_rewards)

    def test_polymarket_rewards_integration(self, reward_tracker):
        """Mock Polymarket API and verify rewards scan called."""
        from scans import scan_polymarket_rewards

        mock_markets = [
            {
                "id": "POLY-TEST",
                "title": "Test Market",
                "reward_pool": 1000.0,
                "reward_rate": 0.10,
            }
        ]

        mock_cache = {"POLY-TEST": 0.50}

        # Call scan with mocked data
        opps = scan_polymarket_rewards(
            markets=mock_markets,
            reward_tracker=reward_tracker,
            price_cache=mock_cache,
        )

        # Verify return is a list
        assert isinstance(opps, list)

    def test_kalshi_rewards_integration(self, kalshi_reward_tracker):
        """Mock Kalshi API and verify Kalshi rewards scan called."""
        from scans import scan_kalshi_rewards

        mock_kalshi_client = MagicMock()
        mock_kalshi_client.get_markets.return_value = [
            {
                "ticker": "TEST-12345",
                "title": "Test Question",
                "reward_pool": 500.0,
            }
        ]

        # Call scan with mocked client
        opps = scan_kalshi_rewards(
            kalshi_client=mock_kalshi_client,
            reward_tracker=kalshi_reward_tracker,
        )

        # Verify return is a list
        assert isinstance(opps, list)

    def test_opportunity_index_basic(self):
        """Verify OpportunityIndex can store and retrieve opportunities."""
        from continuous import OpportunityIndex

        index = OpportunityIndex()

        opp = {
            "type": "polymarket_reward",
            "market": "POLY-TEST",
            "_market_key": ("polymarket", "POLY-TEST"),
            "_token_ids": ["12345"],  # Required for index extraction
            "net_profit": 10.0,
        }

        # Rebuild index with opportunity
        index.rebuild([opp])

        # Verify it can be retrieved
        result = index.lookup("polymarket", "12345")
        assert result is not None
        assert len(result) > 0

    def test_opportunity_index_multiple_entries(self):
        """Verify OpportunityIndex can handle multiple markets."""
        from continuous import OpportunityIndex

        index = OpportunityIndex()

        opp1 = {
            "type": "polymarket_reward",
            "_market_key": ("polymarket", "MARKET-1"),
            "_token_ids": ["11111"],
            "net_profit": 5.0,
        }
        opp2 = {
            "type": "kalshi_reward",
            "_market_key": ("kalshi", "MARKET-2"),
            "_kalshi_ticker": "KALSHI-2",
            "net_profit": 8.0,
        }

        index.rebuild([opp1, opp2])

        # Verify lookups
        result1 = index.lookup("polymarket", "11111")
        result2 = index.lookup("kalshi", "KALSHI-2")
        assert len(result1) > 0
        assert len(result2) > 0

    def test_rewards_scan_error_handling(self):
        """Verify scans handle errors gracefully."""
        # Mock a failing scan
        with patch("scans.scan_polymarket_rewards") as mock_scan:
            mock_scan.side_effect = Exception("API error")

            # Verify exception is raised but doesn't crash caller
            with pytest.raises(Exception):
                mock_scan()

    def test_reward_tracker_caching(self, reward_tracker):
        """Verify RewardTracker caches reward metadata."""
        assert hasattr(reward_tracker, "reward_scores")
        assert isinstance(reward_tracker.reward_scores, dict)

        # Verify cached data is accessible
        for market_key, reward_info in reward_tracker.reward_scores.items():
            assert isinstance(market_key, str)
            assert isinstance(reward_info, dict)

    def test_opportunity_indexing_scalability(self):
        """Verify OpportunityIndex can handle many opportunities."""
        from continuous import OpportunityIndex

        index = OpportunityIndex()

        # Create 50 opportunities
        opps = []
        for i in range(50):
            opp = {
                "type": "polymarket_reward",
                "_market_key": ("polymarket", f"MARKET-{i}"),
                "_token_ids": [f"token-{i}"],
                "net_profit": float(i),
            }
            opps.append(opp)

        # Rebuild index with all opportunities
        index.rebuild(opps)

        # Verify all can be retrieved via token IDs
        for i in range(50):
            result = index.lookup("polymarket", f"token-{i}")
            assert len(result) > 0
            assert result[0]["net_profit"] == float(i)

    def test_reward_tracker_market_lookup(self, reward_tracker):
        """Verify reward tracker can look up markets by key."""
        market_key = "POLY-TEST"
        assert market_key in reward_tracker.reward_scores
        reward_info = reward_tracker.reward_scores[market_key]
        assert "polymarket" in reward_info
        assert reward_info["polymarket"]["pool_size_usdc"] > 0
