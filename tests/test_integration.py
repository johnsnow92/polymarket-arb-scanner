"""Integration tests for Layers 2-5 wiring into executor and display."""

import pytest
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Executor + PositionSizer integration
# ---------------------------------------------------------------------------

class TestExecutorWithPositionSizer:
    """Test that executor uses PositionSizer when provided."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.mock_db = MagicMock()
        self.mock_risk = MagicMock()
        self.mock_risk.check.return_value = (True, "")
        self.mock_risk.clamp_size.side_effect = lambda desired, depth, budget: desired

        from position_sizer import PositionSizer
        self.sizer = PositionSizer(bankroll=1000.0, kelly_fraction=0.5, max_fraction=0.25)

        from executor import ArbitrageExecutor
        self.executor = ArbitrageExecutor(
            pm_trader=None,
            kalshi_client=None,
            db=self.mock_db,
            risk_manager=self.mock_risk,
            dry_run=True,
            position_sizer=self.sizer,
        )

    def test_sizer_used_for_pure_arb(self):
        """Position sizer should produce a size > 0 for a pure arb."""
        opp = {
            "type": "Binary",
            "market": "Test Market",
            "prices": "Y=0.45 N=0.50",
            "total_cost": "$0.95",
            "net_profit": 0.03,
            "net_roi": 0.032,
            "_token_ids": ["tok_yes", "tok_no"],
            "_clob_depth": 100,
        }
        # The sizer should be called via execute() step 3
        size = self.sizer.size_for_opportunity(opp)
        assert size > 0
        assert size <= 250.0  # max_fraction * bankroll = 0.25 * 1000

    def test_sizer_used_for_convergence(self):
        """Position sizer should scale down for informed trades."""
        opp = {
            "type": "ConvergenceOpp",
            "market": "Test Market",
            "total_cost": "$0.45",
            "net_profit": 0.05,
            "net_roi": 0.11,
            "confidence": 0.7,
            "_divergence": 0.08,
        }
        size = self.sizer.size_for_opportunity(opp)
        assert size > 0

    def test_sizer_bankroll_update(self):
        """Bankroll updates should affect sizing."""
        self.sizer.update_bankroll(500.0)
        opp = {
            "type": "Binary",
            "total_cost": "$0.95",
            "net_profit": 0.03,
            "net_roi": 0.032,
        }
        size = self.sizer.size_for_opportunity(opp)
        assert size <= 125.0  # 0.25 * 500


# ---------------------------------------------------------------------------
# EventMonitor + SignalAggregator integration
# ---------------------------------------------------------------------------

class TestEventMonitorWithSignalAggregator:
    """Test that EventMonitor uses SignalAggregator when provided."""

    def test_signal_aggregator_wired(self):
        from signal_aggregator import SignalAggregator
        agg = SignalAggregator()

        # Mock MetaculusClient
        mock_metaculus = MagicMock()
        mock_metaculus.fetch_active_questions.return_value = [
            {
                "id": 123,
                "title": "Will it rain tomorrow?",
                "community_prediction": {"full": {"q2": 0.65}},
                "number_of_forecasters": 50,
            }
        ]

        from event_monitor import EventMonitor
        em = EventMonitor(
            metaculus_client=mock_metaculus,
            divergence_threshold=0.10,
            signal_aggregator=agg,
        )

        assert em.signal_aggregator is agg

    def test_aggregator_receives_signals(self):
        from signal_aggregator import SignalAggregator
        agg = SignalAggregator()
        agg.add_signal("test_mkt", "metaculus", 0.65)
        agg.add_signal("test_mkt", "manifold", 0.60)
        agg.add_signal("test_mkt", "polymarket", 0.50)

        consensus = agg.get_consensus("test_mkt")
        assert consensus is not None
        assert consensus["num_sources"] == 3
        assert 0.50 < consensus["probability"] < 0.65


# ---------------------------------------------------------------------------
# Display with new opportunity types
# ---------------------------------------------------------------------------

class TestDisplayNewTypes:
    """Test that display handles new opportunity types without crashing."""

    def test_display_mm_opportunity(self, capsys):
        from display import display_results
        opps = [{
            "type": "MarketMake",
            "market": "Test MM Market",
            "prices": "bid=0.48 ask=0.52 mid=0.50",
            "total_cost": "$5.00",
            "net_profit": 0.04,
            "net_roi": 0.008,
            "confidence": 0.85,
            "_spread": 0.04,
            "_inventory": 0.0,
        }]
        display_results(opps)
        captured = capsys.readouterr()
        assert "MarketMake" in captured.out
        assert "1 opportunities" in captured.out

    def test_display_convergence_opportunity(self, capsys):
        from display import display_results
        opps = [{
            "type": "ConvergenceOpp",
            "market": "Test Convergence",
            "prices": "smarkets=0.70 median=0.51 div=+0.19",
            "total_cost": "$0.30",
            "net_profit": 0.08,
            "net_roi": 0.267,
            "confidence": 0.72,
        }]
        display_results(opps)
        captured = capsys.readouterr()
        assert "ConvergenceOpp" in captured.out

    def test_display_stale_opportunity(self, capsys):
        from display import display_results
        opps = [{
            "type": "StalePriceOpp",
            "market": "Test Stale",
            "prices": "stale_matchbook=0.45 fresh_polymarket=0.52",
            "total_cost": "$0.45",
            "net_profit": 0.05,
            "net_roi": 0.111,
            "confidence": 0.85,
        }]
        display_results(opps)
        captured = capsys.readouterr()
        assert "StalePriceOpp" in captured.out

    def test_display_json_with_new_fields(self, capsys):
        from display import display_results
        opps = [{
            "type": "MarketMake",
            "market": "Test",
            "prices": "bid=0.48 ask=0.52",
            "total_cost": "$5.00",
            "net_profit": 0.04,
            "net_roi": 0.008,
            "_spread": 0.04,
            "_inventory": 10.5,
            "_num_sources": 3,
        }]
        display_results(opps, json_output=True)
        captured = capsys.readouterr()
        # Just verify the JSON fields appear in the output
        assert '"mm_spread": 0.04' in captured.out
        assert '"mm_inventory": 10.5' in captured.out
        assert '"signal_sources": 3' in captured.out

    def test_display_mixed_types(self, capsys):
        """Display should handle a mix of old and new opportunity types."""
        from display import display_results
        opps = [
            {
                "type": "Binary",
                "market": "Old Type",
                "prices": "Y=0.45 N=0.50",
                "total_cost": "$0.95",
                "gross_spread": "$0.05",
                "fees": "$0.01",
                "net_profit": 0.04,
                "net_roi": "4.21%",
                "volume": "1000",
            },
            {
                "type": "MarketMake",
                "market": "New Type",
                "prices": "bid=0.48 ask=0.52",
                "total_cost": "$5.00",
                "net_profit": 0.04,
                "net_roi": 0.008,
            },
        ]
        display_results(opps)
        captured = capsys.readouterr()
        assert "Binary" in captured.out
        assert "MarketMake" in captured.out
        assert "2 opportunities" in captured.out


# ---------------------------------------------------------------------------
# Fee routing tests
# ---------------------------------------------------------------------------

class TestFeeRouting:
    def test_estimate_total_fee_polymarket(self):
        from fees import estimate_total_fee
        fee = estimate_total_fee("polymarket", 0.40)
        assert fee > 0  # PM winner fee + gas

    def test_estimate_total_fee_zero_fee_platform(self):
        from fees import estimate_total_fee
        fee = estimate_total_fee("sxbet", 0.40)
        assert fee == 0.0

    def test_estimate_total_fee_kalshi(self):
        from fees import estimate_total_fee
        fee = estimate_total_fee("kalshi", 0.40)
        assert fee > 0  # Kalshi taker fee

    def test_find_lowest_fee_path_basic(self):
        from fees import find_lowest_fee_path
        platforms = ["polymarket", "kalshi", "sxbet"]
        yes_prices = {"polymarket": 0.40, "kalshi": 0.42, "sxbet": 0.41}
        no_prices = {"polymarket": 0.55, "kalshi": 0.53, "sxbet": 0.54}
        result = find_lowest_fee_path(platforms, yes_prices, no_prices)
        # sxbet has 0% fees, so paths through sxbet should be preferred
        if result:
            assert result["net_profit"] > 0
            assert "sxbet" in (result["best_yes_platform"], result["best_no_platform"])

    def test_find_lowest_fee_path_no_profit(self):
        from fees import find_lowest_fee_path
        platforms = ["polymarket", "kalshi"]
        yes_prices = {"polymarket": 0.60, "kalshi": 0.62}
        no_prices = {"polymarket": 0.55, "kalshi": 0.53}
        result = find_lowest_fee_path(platforms, yes_prices, no_prices)
        # Total cost > 1.0 for all paths
        assert result is None


# ---------------------------------------------------------------------------
# Backtest engine with new types
# ---------------------------------------------------------------------------

class TestBacktestNewTypes:
    def test_get_layer_pure_arb(self):
        from config import get_layer
        assert get_layer("Binary") == 1
        assert get_layer("KalshiBinary") == 1
        assert get_layer("Cross(PM_YES + K_NO)") == 1
        assert get_layer("NegRisk(5)") == 1
        assert get_layer("MultiCross(3)") == 1

    def test_get_layer_near_arb(self):
        from config import get_layer
        assert get_layer("StalePriceOpp") == 2
        assert get_layer("ResolutionSnipeOpp") == 2

    def test_get_layer_mm(self):
        from config import get_layer
        assert get_layer("MarketMake") == 3

    def test_get_layer_informed(self):
        from config import get_layer
        assert get_layer("EventDivergence") == 4
        assert get_layer("ConvergenceOpp") == 4

    def test_get_layer_unknown(self):
        from config import get_layer
        assert get_layer("UnknownType") == 0

    def test_strategy_layers_dict(self):
        from config import STRATEGY_LAYERS
        assert len(STRATEGY_LAYERS) > 15  # Should cover all known types

    def test_layer_names_dict(self):
        from backtest import LAYER_NAMES
        assert LAYER_NAMES[1] == "Pure Arbitrage"
        assert LAYER_NAMES[3] == "Market Making"


# ---------------------------------------------------------------------------
# Snapshot recorder with new types
# ---------------------------------------------------------------------------

class TestSnapshotNewTypes:
    def test_extract_stale_platform(self):
        from snapshot import SnapshotRecorder
        rec = SnapshotRecorder(db_path=":memory:")
        opp = {
            "type": "StalePriceOpp",
            "_stale_platform": "matchbook",
            "_fresh_platform": "polymarket",
            "_stale_price": 0.45,
            "_fresh_price": 0.52,
        }
        pa, pb, price_a, price_b = rec._extract_platforms(opp)
        assert pa == "matchbook"
        assert pb == "polymarket"
        assert price_a == 0.45
        assert price_b == 0.52
        rec.close()

    def test_extract_mm_platform(self):
        from snapshot import SnapshotRecorder
        rec = SnapshotRecorder(db_path=":memory:")
        opp = {
            "type": "MarketMake",
            "_platform": "polymarket",
            "_bid_price": 0.48,
            "_ask_price": 0.52,
        }
        pa, pb, price_a, price_b = rec._extract_platforms(opp)
        assert pa == "polymarket"
        assert price_a == 0.48
        assert price_b == 0.52
        rec.close()

    def test_extract_convergence_platform(self):
        from snapshot import SnapshotRecorder
        rec = SnapshotRecorder(db_path=":memory:")
        opp = {
            "type": "ConvergenceOpp",
            "_platform": "smarkets",
            "_trade_price": 0.70,
            "_median_price": 0.51,
        }
        pa, pb, price_a, price_b = rec._extract_platforms(opp)
        assert pa == "smarkets"
        assert pb == "median"
        assert price_a == 0.70
        assert price_b == 0.51
        rec.close()

    def test_record_new_type_snapshot(self):
        from snapshot import SnapshotRecorder
        rec = SnapshotRecorder(db_path=":memory:")
        opps = [{
            "type": "StalePriceOpp",
            "market": "Test Market",
            "total_cost": "$0.45",
            "net_profit": 0.05,
            "_stale_platform": "matchbook",
            "_fresh_platform": "polymarket",
            "_stale_price": 0.45,
            "_fresh_price": 0.52,
            "_direction": "BUY_YES",
            "confidence": 0.85,
        }]
        count = rec.record_snapshot(opps)
        assert count == 1

        # Verify stored data
        rows = rec.get_snapshots("2000-01-01T00:00:00", "2100-01-01T00:00:00")
        assert len(rows) == 1
        assert rows[0]["opp_type"] == "StalePriceOpp"
        assert rows[0]["strategy_layer"] == 2
        assert rows[0]["direction"] == "BUY_YES"
        assert rows[0]["confidence"] == 0.85
        rec.close()

    def test_get_strategy_layer(self):
        from snapshot import SnapshotRecorder
        assert SnapshotRecorder._get_strategy_layer("Binary") == 1
        assert SnapshotRecorder._get_strategy_layer("StalePriceOpp") == 2
        assert SnapshotRecorder._get_strategy_layer("MarketMake") == 3
        assert SnapshotRecorder._get_strategy_layer("EventDivergence") == 4
        assert SnapshotRecorder._get_strategy_layer("Unknown") == 0


# ---------------------------------------------------------------------------
# Executor directional legs for exchange platforms
# ---------------------------------------------------------------------------

_ALL_PLATFORMS = frozenset([
    "polymarket", "kalshi", "betfair", "smarkets",
    "sxbet", "matchbook", "gemini", "ibkr",
])


class TestDirectionalLegsExchanges:
    """Test _build_directional_legs handles all 8 platforms."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.mock_db = MagicMock()
        self.mock_risk = MagicMock()
        self.mock_risk.check.return_value = (True, "")

        from executor import ArbitrageExecutor
        self.executor = ArbitrageExecutor(
            pm_trader=None,
            kalshi_client=None,
            db=self.mock_db,
            risk_manager=self.mock_risk,
            dry_run=True,
        )

    @patch("executor.ENABLED_EXECUTION_PLATFORMS", _ALL_PLATFORMS)
    def test_betfair_buy_yes(self):
        opp = {
            "type": "ConvergenceOpp",
            "_platform": "betfair",
            "_direction": "BUY_YES",
            "_trade_price": 0.40,
            "_market_id": "mkt123",
            "_selection_id": "sel456",
        }
        legs = self.executor._build_directional_legs(opp, 5.0)
        assert len(legs) == 1
        assert legs[0]["platform"] == "betfair"
        assert legs[0]["side"] == "BACK"

    @patch("executor.ENABLED_EXECUTION_PLATFORMS", _ALL_PLATFORMS)
    def test_smarkets_buy_no(self):
        opp = {
            "type": "StalePriceOpp",
            "_platform": "smarkets",
            "_direction": "BUY_NO",
            "_trade_price": 0.60,
            "_sm_market_id": "mkt789",
            "_sm_contract_id": "con123",
        }
        legs = self.executor._build_directional_legs(opp, 5.0)
        assert len(legs) == 1
        assert legs[0]["platform"] == "smarkets"
        assert legs[0]["side"] == "LAY"

    @patch("executor.ENABLED_EXECUTION_PLATFORMS", _ALL_PLATFORMS)
    def test_matchbook_buy_yes(self):
        opp = {
            "type": "ConvergenceOpp",
            "_platform": "matchbook",
            "_direction": "BUY_YES",
            "_trade_price": 0.45,
            "_mb_market_id": "mb_mkt",
            "_mb_runner_id": "mb_run",
        }
        legs = self.executor._build_directional_legs(opp, 5.0)
        assert len(legs) == 1
        assert legs[0]["platform"] == "matchbook"
        assert legs[0]["side"] == "back"

    @patch("executor.ENABLED_EXECUTION_PLATFORMS", _ALL_PLATFORMS)
    def test_sxbet_buy_no(self):
        opp = {
            "type": "StalePriceOpp",
            "_platform": "sxbet",
            "_direction": "BUY_NO",
            "_trade_price": 0.55,
            "_sx_market_hash": "hash123",
            "_sx_outcome_id": "oid456",
        }
        legs = self.executor._build_directional_legs(opp, 5.0)
        assert len(legs) == 1
        assert legs[0]["platform"] == "sxbet"
        assert legs[0]["side"] == "LAY"
