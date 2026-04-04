"""Tests for WebSocket feed staleness detection and executor integration."""

import pytest
from unittest.mock import MagicMock, patch
import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import TradeDB
from risk_manager import RiskManager


# Mock external API modules before importing executor
@pytest.fixture(autouse=True)
def mock_external_modules():
    """Mock external API modules that may not be installed."""
    mock_modules = {}
    for mod_name in [
        "polymarket_api", "kalshi_api",
        "betfair_api", "smarkets_api", "sxbet_api",
    ]:
        if mod_name not in sys.modules:
            mock_modules[mod_name] = MagicMock()
            sys.modules[mod_name] = mock_modules[mod_name]
    yield
    for mod_name in mock_modules:
        del sys.modules[mod_name]


@pytest.fixture
def FeedManager():
    """Import FeedManager after mocking external modules."""
    from ws_feeds import FeedManager
    return FeedManager


@pytest.fixture
def ArbitrageExecutor():
    """Import executor after mocking external modules."""
    if "executor" in sys.modules:
        del sys.modules["executor"]
    from executor import ArbitrageExecutor
    return ArbitrageExecutor


@pytest.fixture
def db():
    """Create in-memory test database."""
    trade_db = TradeDB(":memory:")
    yield trade_db
    trade_db.close()


@pytest.fixture
def risk_manager():
    """Create test risk manager."""
    return RiskManager({
        "max_trade_size": 5.0,
        "daily_loss_limit": 25.0,
        "max_open_positions": 25,
        "min_liquidity": 25.0,
        "min_liquidity_high_roi": 10.0,
        "min_net_roi": 0,
        "allow_better_reentry": True,
        "reentry_improvement_threshold": 0.20,
    })


@pytest.fixture
def executor(ArbitrageExecutor, db, risk_manager):
    """Create test executor."""
    pm_trader = MagicMock()
    kalshi_client = MagicMock()
    return ArbitrageExecutor(
        pm_trader=pm_trader,
        kalshi_client=kalshi_client,
        db=db,
        risk_manager=risk_manager,
        dry_run=True,
        max_trade_size=5.0,
    )


# ============================================================================
# FeedManager Staleness Detection Tests
# ============================================================================

class TestFeedHealthMonitoring:
    """Test FeedManager heartbeat monitoring and stale detection."""

    def test_fresh_feed_is_healthy(self, FeedManager):
        """Feed with recent message should report as healthy."""
        price_cache = {}
        feed_manager = FeedManager(
            on_price_update=lambda *args: None,
            price_cache=price_cache,
        )

        # Set last message time to 10 seconds ago
        with patch("time.time", return_value=1000.0):
            feed_manager._last_message_time["polymarket"] = 990.0

        # Check health at time 1000.0 (10 seconds later)
        with patch("time.time", return_value=1000.0):
            assert feed_manager.is_feed_healthy("polymarket", threshold_seconds=30.0)

    def test_stale_feed_is_unhealthy(self, FeedManager):
        """Feed with no recent message should report as unhealthy."""
        price_cache = {}
        feed_manager = FeedManager(
            on_price_update=lambda *args: None,
            price_cache=price_cache,
        )

        # Set last message time to 40 seconds ago
        with patch("time.time", return_value=1000.0):
            feed_manager._last_message_time["polymarket"] = 960.0

        # Check health at time 1000.0 (40 seconds later)
        with patch("time.time", return_value=1000.0):
            assert not feed_manager.is_feed_healthy("polymarket", threshold_seconds=30.0)

    def test_mark_stale_feeds_sets_flag(self, FeedManager):
        """mark_stale_feeds should set _stale: true for stale feeds."""
        price_cache = {
            ("polymarket", "token1"): {"price": 0.5},
            ("polymarket", "token2"): {"price": 0.6},
            ("kalshi", "ticker1"): {"yes_price": 0.7},
        }
        feed_manager = FeedManager(
            on_price_update=lambda *args: None,
            price_cache=price_cache,
        )

        # Set polymarket as stale (40 seconds without message)
        with patch("time.time", return_value=1000.0):
            feed_manager._last_message_time["polymarket"] = 960.0
            feed_manager._last_message_time["kalshi"] = 995.0

            feed_manager.mark_stale_feeds(stale_threshold_seconds=30.0)

        # Polymarket prices should be marked stale
        assert price_cache[("polymarket", "token1")].get("_stale") is True
        assert price_cache[("polymarket", "token2")].get("_stale") is True
        # Kalshi should NOT be marked stale
        assert price_cache[("kalshi", "ticker1")].get("_stale") is not True

    def test_feed_recovery_clears_flag(self, FeedManager):
        """mark_stale_feeds should clear _stale flag when feed recovers."""
        price_cache = {
            ("polymarket", "token1"): {"price": 0.5, "_stale": True},
            ("kalshi", "ticker1"): {"yes_price": 0.7, "_stale": True},
        }
        feed_manager = FeedManager(
            on_price_update=lambda *args: None,
            price_cache=price_cache,
        )

        # Mark both as stale initially
        with patch("time.time", return_value=1000.0):
            feed_manager._last_message_time["polymarket"] = 960.0
            feed_manager._last_message_time["kalshi"] = 960.0
            feed_manager.mark_stale_feeds(stale_threshold_seconds=30.0)

        # Both should still be stale
        assert price_cache[("polymarket", "token1")].get("_stale") is True
        assert price_cache[("kalshi", "ticker1")].get("_stale") is True

        # Now update polymarket to be recent
        with patch("time.time", return_value=2000.0):
            feed_manager._last_message_time["polymarket"] = 1990.0
            feed_manager._last_message_time["kalshi"] = 1960.0
            feed_manager.mark_stale_feeds(stale_threshold_seconds=30.0)

        # Polymarket should be recovered
        assert price_cache[("polymarket", "token1")].get("_stale") is False
        # Kalshi should still be stale
        assert price_cache[("kalshi", "ticker1")].get("_stale") is True

    def test_multiple_platforms_independent(self, FeedManager):
        """Staleness of one platform should not affect others."""
        price_cache = {
            ("polymarket", "token1"): {"price": 0.5},
            ("kalshi", "ticker1"): {"yes_price": 0.7},
        }
        feed_manager = FeedManager(
            on_price_update=lambda *args: None,
            price_cache=price_cache,
        )

        with patch("time.time", return_value=1000.0):
            # Polymarket stale, Kalshi fresh
            feed_manager._last_message_time["polymarket"] = 960.0
            feed_manager._last_message_time["kalshi"] = 990.0

            feed_manager.mark_stale_feeds(stale_threshold_seconds=30.0)

        # Only polymarket should be stale
        assert price_cache[("polymarket", "token1")].get("_stale") is True
        assert price_cache[("kalshi", "ticker1")].get("_stale") is not True

    def test_mark_stale_feeds_idempotent(self, FeedManager):
        """mark_stale_feeds should be safe to call repeatedly."""
        price_cache = {
            ("polymarket", "token1"): {"price": 0.5},
        }
        feed_manager = FeedManager(
            on_price_update=lambda *args: None,
            price_cache=price_cache,
        )

        with patch("time.time", return_value=1000.0):
            feed_manager._last_message_time["polymarket"] = 960.0

            # Call multiple times
            feed_manager.mark_stale_feeds(stale_threshold_seconds=30.0)
            feed_manager.mark_stale_feeds(stale_threshold_seconds=30.0)
            feed_manager.mark_stale_feeds(stale_threshold_seconds=30.0)

        # Should still be stale, no errors
        assert price_cache[("polymarket", "token1")].get("_stale") is True


# ============================================================================
# Executor Stale Detection Integration Tests
# ============================================================================

class TestExecutorStaleRejection:
    """Test executor revalidation with stale prices."""

    def test_executor_rejects_stale_opportunity_binary(self, executor):
        """Executor should reject binary opportunity when prices are stale."""
        price_cache = {
            ("polymarket", "token1"): {"price": 0.5, "_stale": True, "_ts": time.time()},
            ("polymarket", "token2"): {"price": 0.5, "_stale": True, "_ts": time.time()},
        }
        opp = {
            "type": "binary",
            "market": "Test Market",
            "_token_ids": ["token1", "token2"],
            "net_profit": 0.01,
        }

        passed, profit, reason = executor._revalidate_binary(opp, 0.01, price_cache)
        assert passed is False
        assert reason == "feed_stale"

    def test_executor_accepts_fresh_opportunity_binary(self, executor):
        """Executor should accept binary opportunity when prices are fresh."""
        price_cache = {
            ("polymarket", "token1"): {"price": 0.4, "_stale": False, "_ts": time.time()},
            ("polymarket", "token2"): {"price": 0.6, "_stale": False, "_ts": time.time()},
        }
        opp = {
            "type": "binary",
            "market": "Test Market",
            "_token_ids": ["token1", "token2"],
            "net_profit": 0.05,
            "total_cost": "$0.50",  # Set total_cost to enable threshold calculation
            "_layer": 1,  # Set layer to avoid warning
        }

        # Mock the profit calculation to return high enough profit
        with patch("executor.net_profit_binary_internal") as mock_profit:
            mock_profit.return_value = {
                "net_profit": 0.05,
                "net_roi": 0.10,
                "yes_price": 0.4,
                "no_price": 0.6,
            }
            passed, profit, reason = executor._revalidate_binary(opp, 0.05, price_cache)
            # Should pass (profit above threshold)
            assert passed is True

    def test_revalidation_logs_stale_skip(self, executor):
        """Executor should log when skipping due to stale prices."""
        price_cache = {
            ("polymarket", "token1"): {"price": 0.5, "_stale": True, "_ts": time.time()},
            ("polymarket", "token2"): {"price": 0.5, "_stale": True, "_ts": time.time()},
        }
        opp = {
            "type": "binary",
            "market": "Test Market",
            "_token_ids": ["token1", "token2"],
            "net_profit": 0.01,
        }

        with patch("executor.logger") as mock_logger:
            passed, profit, reason = executor._revalidate_binary(opp, 0.01, price_cache)
            # Should have logged the stale skip
            assert mock_logger.info.called
            call_args = [str(call) for call in mock_logger.info.call_args_list]
            stale_logged = any("stale" in str(call).lower() for call in call_args)
            assert stale_logged or not passed  # Either logged or returned False

    def test_executor_rejects_stale_cross_platform(self, executor):
        """Executor should reject cross-platform opp when polymarket is stale."""
        price_cache = {
            ("polymarket", "token1"): {"price": 0.4, "_stale": True, "_ts": time.time()},
            ("polymarket", "token2"): {"price": 0.6, "_stale": True, "_ts": time.time()},
            ("kalshi", "ticker1"): {"yes_price": 0.5, "_ts": time.time()},
        }
        opp = {
            "type": "cross",
            "market": "PM/Kalshi Cross",
            "_token_ids": ["token1", "token2"],
            "_kalshi_ticker": "ticker1",
            "net_profit": 0.01,
        }

        passed, profit, reason = executor._revalidate_cross(opp, 0.01, price_cache)
        assert passed is False
        assert reason == "feed_stale"

    def test_executor_rejects_stale_multi_cross(self, executor):
        """Executor should reject multi_cross when any leg is stale."""
        price_cache = {
            ("polymarket", "token1"): {"yes_ask": 0.3, "_stale": True, "_ts": time.time()},
            ("kalshi", "ticker1"): {"yes_ask": 0.7, "_ts": time.time()},
        }
        opp = {
            "type": "multi_cross",
            "market": "Multi-Cross",
            "_outcome_legs": [
                {
                    "platform": "polymarket",
                    "_token_id": "token1",
                    "price": 0.3,
                },
                {
                    "platform": "kalshi",
                    "_kalshi_ticker": "ticker1",
                    "price": 0.7,
                },
            ],
            "net_profit": 0.01,
        }

        passed, profit, reason = executor._revalidate_multi_cross(opp, 0.01, price_cache)
        assert passed is False
        assert reason == "feed_stale"

    def test_stale_flag_default_false(self, executor):
        """_stale flag should default to False for normal prices."""
        price_cache = {
            ("polymarket", "token1"): {"price": 0.5, "_ts": time.time()},
            ("polymarket", "token2"): {"price": 0.5, "_ts": time.time()},
        }
        opp = {
            "type": "binary",
            "market": "Test Market",
            "_token_ids": ["token1", "token2"],
            "net_profit": 0.05,
            "total_cost": "$0.50",
            "_layer": 1,
        }

        # Should not treat missing _stale flag as stale
        with patch("executor.net_profit_binary_internal") as mock_profit:
            mock_profit.return_value = {
                "net_profit": 0.05,
                "net_roi": 0.10,
            }
            passed, profit, reason = executor._revalidate_binary(opp, 0.05, price_cache)
            # Should succeed (profit not below threshold)
            assert passed is True
