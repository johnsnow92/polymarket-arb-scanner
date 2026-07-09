"""
Integration tests for executor strategy dispatch (_build_legs and _revalidate).

Tests the 4 new market signal strategies:
- STRAT-01: Order Book Imbalance
- STRAT-02: News-Driven Resolution Sniping
- STRAT-06: Correlated Market Pairs
- STRAT-07: Time Decay Convergence
"""

import sys
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, str(sys.path[0]).rsplit(
    "tests", 1)[0])

# Mock external dependencies before importing executor
sys.modules["ib_insync"] = MagicMock()
py_clob_mock = MagicMock()
sys.modules["py_clob_client_v2"] = py_clob_mock
sys.modules["py_clob_client_v2.client"] = MagicMock()
sys.modules["py_clob_client_v2.clob_types"] = MagicMock()
sys.modules["py_clob_client_v2.http_helpers"] = MagicMock()
sys.modules["py_clob_client_v2.http_helpers.helpers"] = MagicMock()
py_clob_mock.client = sys.modules["py_clob_client_v2.client"]
py_clob_mock.clob_types = sys.modules["py_clob_client_v2.clob_types"]


class TestImbalanceStrategy(unittest.TestCase):
    """Tests for STRAT-01: Order Book Imbalance."""

    def setUp(self):
        """Import executor after mocks are in place."""
        from executor import ArbitrageExecutor
        self.ArbitrageExecutor = ArbitrageExecutor

    def _make_executor(self):
        """Create a mocked executor instance."""
        return self.ArbitrageExecutor(
            pm_trader=MagicMock(),
            kalshi_client=MagicMock(),
            db=MagicMock(),
            risk_manager=MagicMock(),
        )

    def test_build_legs_imbalance_buy_yes(self):
        """Test _build_legs for imbalance when direction is YES."""
        executor = self._make_executor()
        opp = {
            "type": "Imbalance",
            "market": "Bitcoin over $100k?",
            "platform": "polymarket",
            "_direction": "YES",
            "_token_ids": ["token_yes_123", "token_no_456"],
            "_yes_price": 0.55,
        }
        legs = executor._build_legs(opp, size=5.0)

        self.assertEqual(len(legs), 1)
        leg = legs[0]
        self.assertEqual(leg["platform"], "polymarket")
        self.assertEqual(leg["side"], "BUY")
        self.assertEqual(leg["_token_id"], "token_yes_123")

    def test_build_legs_imbalance_buy_no(self):
        """Test _build_legs for imbalance when direction is NO."""
        executor = self._make_executor()
        opp = {
            "type": "Imbalance",
            "market": "Bitcoin over $100k?",
            "platform": "polymarket",
            "_direction": "NO",
            "_token_ids": ["token_yes_123", "token_no_456"],
            "_no_price": 0.45,
        }
        legs = executor._build_legs(opp, size=5.0)

        self.assertEqual(len(legs), 1)
        leg = legs[0]
        self.assertEqual(leg["side"], "BUY")
        self.assertEqual(leg["_token_id"], "token_no_456")

    def test_revalidate_imbalance_ratio_stable(self):
        """Test revalidate for imbalance when ratio hasn't collapsed."""
        executor = self._make_executor()
        opp = {
            "type": "Imbalance",
            "market": "Bitcoin over $100k?",
            "net_profit": 0.50,  # $0.50 profit
            "total_cost": "$10.00",  # $10 position
            "_imbalance_ratio": 3.2,  # ~8.5% drop from original 3.5, < 30% threshold
        }
        result = executor._revalidate(opp)
        self.assertTrue(result)

    def test_revalidate_imbalance_ratio_collapsed(self):
        """Test revalidate for imbalance when ratio has collapsed >30%."""
        executor = self._make_executor()
        opp = {
            "type": "Imbalance",
            "market": "Bitcoin over $100k?",
            "net_profit": 0.50,
            "total_cost": "$10.00",
            "_imbalance_ratio": 2.0,  # Current ratio
            "_original_imbalance_ratio": 3.0,  # 33% drop from original, > 30% threshold
        }
        result = executor._revalidate(opp)
        self.assertFalse(result)

    def test_revalidate_imbalance_zero_original_ratio(self):
        """Test revalidate handles zero imbalance ratio gracefully."""
        executor = self._make_executor()
        opp = {
            "type": "Imbalance",
            "market": "Bitcoin over $100k?",
            "net_profit": 0.50,
            "total_cost": "$10.00",
            "_imbalance_ratio": 0,
        }
        result = executor._revalidate(opp)
        # Should return True (passed=True by default when ratio is 0)
        self.assertTrue(result)


class TestNewsSnipeStrategy(unittest.TestCase):
    """Tests for STRAT-02: News-Driven Resolution Sniping."""

    def setUp(self):
        """Import executor after mocks are in place."""
        from executor import ArbitrageExecutor
        self.ArbitrageExecutor = ArbitrageExecutor

    def _make_executor(self):
        """Create a mocked executor instance."""
        return self.ArbitrageExecutor(
            pm_trader=MagicMock(),
            kalshi_client=MagicMock(),
            db=MagicMock(),
            risk_manager=MagicMock(),
        )

    def test_build_legs_news_snipe_buy_yes(self):
        """Test _build_legs for news snipe when sentiment points to YES."""
        executor = self._make_executor()
        opp = {
            "type": "NewsSnipe",
            "market": "Will the deal be approved?",
            "platform": "polymarket",
            "_sentiment": "YES",
            "_token_ids": ["token_yes_789", "token_no_012"],
            "_yes_price": 0.72,
        }
        legs = executor._build_legs(opp, size=5.0)

        self.assertEqual(len(legs), 1)
        leg = legs[0]
        self.assertEqual(leg["platform"], "polymarket")
        self.assertEqual(leg["side"], "BUY")
        self.assertEqual(leg["_token_id"], "token_yes_789")

    def test_build_legs_news_snipe_buy_no(self):
        """Test _build_legs for news snipe when sentiment points to NO."""
        executor = self._make_executor()
        opp = {
            "type": "NewsSnipe",
            "market": "Will the deal be approved?",
            "platform": "polymarket",
            "_sentiment": "NO",
            "_token_ids": ["token_yes_789", "token_no_012"],
            "_no_price": 0.28,
        }
        legs = executor._build_legs(opp, size=5.0)

        self.assertEqual(len(legs), 1)
        leg = legs[0]
        self.assertEqual(leg["side"], "BUY")
        self.assertEqual(leg["_token_id"], "token_no_012")

    def test_revalidate_news_snipe_confidence_above_threshold(self):
        """Test revalidate for news snipe with sufficient confidence."""
        executor = self._make_executor()
        with patch("executor.NEWS_SNIPE_CONFIDENCE_THRESHOLD", 0.75):
            opp = {
                "type": "NewsSnipe",
                "market": "Will the deal be approved?",
                "net_profit": 0.75,
                "total_cost": "$15.00",
                "_confidence": 0.85,
            }
            result = executor._revalidate(opp)
            self.assertTrue(result)

    def test_revalidate_news_snipe_confidence_below_threshold(self):
        """Test revalidate for news snipe with insufficient confidence."""
        executor = self._make_executor()
        with patch("executor.NEWS_SNIPE_CONFIDENCE_THRESHOLD", 0.75):
            opp = {
                "type": "NewsSnipe",
                "market": "Will the deal be approved?",
                "net_profit": 0.75,
                "total_cost": "$15.00",
                "_confidence": 0.65,
            }
            result = executor._revalidate(opp)
            self.assertFalse(result)

    def test_revalidate_news_snipe_at_threshold(self):
        """Test revalidate for news snipe at exact threshold."""
        executor = self._make_executor()
        with patch("executor.NEWS_SNIPE_CONFIDENCE_THRESHOLD", 0.75):
            opp = {
                "type": "NewsSnipe",
                "market": "Will the deal be approved?",
                "net_profit": 0.75,
                "total_cost": "$15.00",
                "_confidence": 0.75,
            }
            result = executor._revalidate(opp)
            self.assertTrue(result)


class TestCorrelatedStrategy(unittest.TestCase):
    """Tests for STRAT-06: Correlated Market Pairs."""

    def setUp(self):
        """Import executor after mocks are in place."""
        from executor import ArbitrageExecutor
        self.ArbitrageExecutor = ArbitrageExecutor

    def _make_executor(self):
        """Create a mocked executor instance."""
        return self.ArbitrageExecutor(
            pm_trader=MagicMock(),
            kalshi_client=MagicMock(),
            db=MagicMock(),
            risk_manager=MagicMock(),
        )

    def test_build_legs_correlated_long_short(self):
        """Test _build_legs for correlated pairs with long and short legs."""
        executor = self._make_executor()
        opp = {
            "type": "Correlated",
            "market_long": "Bitcoin $100k",
            "market_short": "Bitcoin $90k",
            "platform_long": "polymarket",
            "platform_short": "polymarket",
            "_long_leg": {
                "_token_ids": ["token_long_345"],
                "_yes_price": 0.65,
            },
            "_short_leg": {
                "_token_ids": ["token_short_678"],
                "_yes_price": 0.52,
            },
        }
        legs = executor._build_legs(opp, size=5.0)

        self.assertEqual(len(legs), 2)

        long_leg = next((l for l in legs if l["side"] == "BUY"), None)
        self.assertIsNotNone(long_leg)
        self.assertEqual(long_leg["_token_id"], "token_long_345")

        short_leg = next((l for l in legs if l["side"] == "SELL"), None)
        self.assertIsNotNone(short_leg)
        self.assertEqual(short_leg["_token_id"], "token_short_678")

    def test_revalidate_correlated_spread_stable(self):
        """Test revalidate for correlated when spread hasn't collapsed."""
        executor = self._make_executor()
        opp = {
            "type": "Correlated",
            "market_long": "Bitcoin $100k",
            "market_short": "Bitcoin $90k",
            "_spread": 0.10,  # 10% spread
            "_original_spread": 0.12,  # Original was 12%, ~16.7% decline, < 20%
            "net_profit": 1.50,
            "total_cost": "$20.00",
        }
        result = executor._revalidate(opp)
        self.assertTrue(result)

    def test_revalidate_correlated_spread_collapsed(self):
        """Test revalidate for correlated when spread collapsed >20%."""
        executor = self._make_executor()
        opp = {
            "type": "Correlated",
            "market_long": "Bitcoin $100k",
            "market_short": "Bitcoin $90k",
            "_spread": 0.07,  # Current spread 7%
            "_original_spread": 0.10,  # Original 10%, 30% decline, > 20%
            "net_profit": 1.50,
            "total_cost": "$20.00",
        }
        result = executor._revalidate(opp)
        self.assertFalse(result)


class TestTimeDecayStrategy(unittest.TestCase):
    """Tests for STRAT-07: Time Decay Convergence."""

    def setUp(self):
        """Import executor after mocks are in place."""
        from executor import ArbitrageExecutor
        self.ArbitrageExecutor = ArbitrageExecutor

    def _make_executor(self):
        """Create a mocked executor instance."""
        return self.ArbitrageExecutor(
            pm_trader=MagicMock(),
            kalshi_client=MagicMock(),
            db=MagicMock(),
            risk_manager=MagicMock(),
        )

    def test_build_legs_time_decay_consensus_yes(self):
        """Test _build_legs for time decay when consensus side is YES."""
        executor = self._make_executor()
        opp = {
            "type": "TimeDecay",
            "market": "Will recession hit by 2026?",
            "platform": "polymarket",
            "_consensus_side": "YES",
            "_token_ids": ["token_yes_555", "token_no_666"],
            "_yes_price": 0.80,
        }
        legs = executor._build_legs(opp, size=5.0)

        self.assertEqual(len(legs), 1)
        leg = legs[0]
        self.assertEqual(leg["platform"], "polymarket")
        self.assertEqual(leg["side"], "BUY")
        self.assertEqual(leg["_token_id"], "token_yes_555")

    def test_build_legs_time_decay_consensus_no(self):
        """Test _build_legs for time decay when consensus side is NO."""
        executor = self._make_executor()
        opp = {
            "type": "TimeDecay",
            "market": "Will recession hit by 2026?",
            "platform": "polymarket",
            "_consensus_side": "NO",
            "_token_ids": ["token_yes_555", "token_no_666"],
            "_no_price": 0.12,
        }
        legs = executor._build_legs(opp, size=5.0)

        self.assertEqual(len(legs), 1)
        leg = legs[0]
        self.assertEqual(leg["side"], "BUY")
        self.assertEqual(leg["_token_id"], "token_no_666")

    def test_revalidate_time_decay_consensus_sufficient(self):
        """Test revalidate for time decay with sufficient consensus."""
        executor = self._make_executor()
        with patch("executor.TIME_DECAY_MIN_CONSENSUS", 0.90):
            opp = {
                "type": "TimeDecay",
                "market": "Will recession hit by 2026?",
                "_hours_to_expiry": 36,  # 36 hours until expiry
                "_consensus_prob": 0.92,  # 92% consensus > 90% threshold
                "net_profit": 2.50,
                "total_cost": "$50.00",
            }
            result = executor._revalidate(opp)
            self.assertTrue(result)

    def test_revalidate_time_decay_consensus_insufficient(self):
        """Test revalidate for time decay with insufficient consensus."""
        executor = self._make_executor()
        with patch("executor.TIME_DECAY_MIN_CONSENSUS", 0.90):
            opp = {
                "type": "TimeDecay",
                "market": "Will recession hit by 2026?",
                "_hours_to_expiry": 36,
                "_consensus_prob": 0.88,  # 88% consensus < 90% threshold
                "net_profit": 2.50,
                "total_cost": "$50.00",
            }
            result = executor._revalidate(opp)
            self.assertFalse(result)

    def test_revalidate_time_decay_expired(self):
        """Test revalidate for time decay when market is within 1 hour of resolution."""
        executor = self._make_executor()
        with patch("executor.TIME_DECAY_MIN_CONSENSUS", 0.90):
            opp = {
                "type": "TimeDecay",
                "market": "Will recession hit by 2026?",
                "_hours_to_expiry": 0.5,  # 30 minutes until expiry < 1 hour
                "_consensus_prob": 0.95,
                "net_profit": 2.50,
                "total_cost": "$50.00",
            }
            result = executor._revalidate(opp)
            self.assertFalse(result)

    def test_revalidate_time_decay_at_consensus_threshold(self):
        """Test revalidate for time decay at exact consensus threshold."""
        executor = self._make_executor()
        with patch("executor.TIME_DECAY_MIN_CONSENSUS", 0.90):
            opp = {
                "type": "TimeDecay",
                "market": "Will recession hit by 2026?",
                "_hours_to_expiry": 24,
                "_consensus_prob": 0.90,  # Exactly at threshold
                "net_profit": 2.50,
                "total_cost": "$50.00",
            }
            result = executor._revalidate(opp)
            self.assertTrue(result)


class TestStrategyDispatch(unittest.TestCase):
    """Tests for strategy dispatch and type routing."""

    def setUp(self):
        """Import executor after mocks are in place."""
        from executor import ArbitrageExecutor
        self.ArbitrageExecutor = ArbitrageExecutor

    def _make_executor(self):
        """Create a mocked executor instance."""
        return self.ArbitrageExecutor(
            pm_trader=MagicMock(),
            kalshi_client=MagicMock(),
            db=MagicMock(),
            risk_manager=MagicMock(),
        )

    def test_build_legs_routes_to_imbalance(self):
        """Test that imbalance type routes to correct handler."""
        executor = self._make_executor()
        opp = {
            "type": "Imbalance",
            "market": "Test",
            "platform": "polymarket",
            "_direction": "YES",
            "_token_ids": ["t1", "t2"],
            "_yes_price": 0.5,
        }
        legs = executor._build_legs(opp, size=5.0)
        self.assertIsInstance(legs, list)

    def test_build_legs_routes_to_news_snipe(self):
        """Test that news-snipe type routes to correct handler."""
        executor = self._make_executor()
        opp = {
            "type": "NewsSnipe",
            "market": "Test",
            "platform": "polymarket",
            "_sentiment": "YES",
            "_token_ids": ["t1", "t2"],
            "_yes_price": 0.5,
        }
        legs = executor._build_legs(opp, size=5.0)
        self.assertIsInstance(legs, list)

    def test_build_legs_routes_to_correlated(self):
        """Test that correlated type routes to correct handler."""
        executor = self._make_executor()
        opp = {
            "type": "Correlated",
            "market_long": "Test",
            "market_short": "Test2",
            "_long_leg": {"_token_ids": ["t1"], "_yes_price": 0.6},
            "_short_leg": {"_token_ids": ["t2"], "_yes_price": 0.5},
        }
        legs = executor._build_legs(opp, size=5.0)
        self.assertIsInstance(legs, list)

    def test_build_legs_routes_to_time_decay(self):
        """Test that time-decay type routes to correct handler."""
        executor = self._make_executor()
        opp = {
            "type": "TimeDecay",
            "market": "Test",
            "platform": "polymarket",
            "_consensus_side": "YES",
            "_token_ids": ["t1", "t2"],
            "_yes_price": 0.8,
        }
        legs = executor._build_legs(opp, size=5.0)
        self.assertIsInstance(legs, list)

    def test_revalidate_routes_to_imbalance(self):
        """Test that imbalance type routes to correct revalidate handler."""
        executor = self._make_executor()
        opp = {
            "type": "Imbalance",
            "market": "Test",
            "_imbalance_ratio": 2.5,
            "net_profit": 0.50,
            "total_cost": "$10.00",
        }
        result = executor._revalidate(opp)
        self.assertIsInstance(result, bool)

    def test_revalidate_routes_to_news_snipe(self):
        """Test that news-snipe type routes to correct revalidate handler."""
        executor = self._make_executor()
        with patch("executor.NEWS_SNIPE_CONFIDENCE_THRESHOLD", 0.75):
            opp = {
                "type": "NewsSnipe",
                "market": "Test",
                "_confidence": 0.80,
                "net_profit": 0.75,
                "total_cost": "$15.00",
            }
            result = executor._revalidate(opp)
            self.assertIsInstance(result, bool)

    def test_revalidate_routes_to_correlated(self):
        """Test that correlated type routes to correct revalidate handler."""
        executor = self._make_executor()
        opp = {
            "type": "Correlated",
            "market_long": "Test",
            "market_short": "Test2",
            "_spread": 0.10,
            "_original_spread": 0.12,
            "net_profit": 1.50,
            "total_cost": "$20.00",
        }
        result = executor._revalidate(opp)
        self.assertIsInstance(result, bool)

    def test_revalidate_routes_to_time_decay(self):
        """Test that time-decay type routes to correct revalidate handler."""
        executor = self._make_executor()
        with patch("executor.TIME_DECAY_MIN_CONSENSUS", 0.90):
            opp = {
                "type": "TimeDecay",
                "market": "Test",
                "_hours_to_expiry": 24,
                "_consensus_prob": 0.92,
                "net_profit": 2.50,
                "total_cost": "$50.00",
            }
            result = executor._revalidate(opp)
            self.assertIsInstance(result, bool)


if __name__ == "__main__":
    unittest.main()
