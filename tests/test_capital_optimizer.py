"""Tests for capital_optimizer.py — Strategies #44-#47."""

import pytest
from unittest.mock import patch, MagicMock
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capital_optimizer import (
    OpportunityCostScorer,
    MarginOptimizer,
    TaxOptimizer,
    WithdrawalTimingOptimizer,
    CapitalOptimizer,
    get_capital_optimizer,
)


class TestOpportunityCostScorer:
    def test_score_basic_annualization(self):
        scorer = OpportunityCostScorer(min_annualized_roi=0.10)
        opp = {"net_roi": 0.10, "_days_to_resolution": 30}
        score = scorer.score(opp)
        expected = 0.10 * (365 / 30)
        assert score == pytest.approx(expected, abs=0.01)

    def test_score_with_string_roi(self):
        scorer = OpportunityCostScorer()
        opp = {"net_roi": "5%", "_days_to_resolution": 10}
        score = scorer.score(opp)
        expected = 0.05 * (365 / 10)
        assert score == pytest.approx(expected, abs=0.01)

    def test_score_zero_days_returns_roi(self):
        scorer = OpportunityCostScorer()
        opp = {"net_roi": 0.15, "_days_to_resolution": 0}
        score = scorer.score(opp)
        assert score == 0.15

    def test_passes_threshold(self):
        with patch("capital_optimizer.OPPORTUNITY_COST_SCORING_ENABLED", True):
            scorer = OpportunityCostScorer(min_annualized_roi=0.50)
            opp_high = {"net_roi": 0.10, "_days_to_resolution": 30}
            opp_low = {"net_roi": 0.01, "_days_to_resolution": 365}

            assert scorer.passes_threshold(opp_high) is True
            assert scorer.passes_threshold(opp_low) is False

    def test_rank_opportunities_sorts_by_annualized(self):
        with patch("capital_optimizer.OPPORTUNITY_COST_SCORING_ENABLED", True):
            scorer = OpportunityCostScorer(min_annualized_roi=0.0)
            opps = [
                {"net_roi": 0.05, "_days_to_resolution": 60},
                {"net_roi": 0.03, "_days_to_resolution": 10},
                {"net_roi": 0.10, "_days_to_resolution": 365},
            ]

            ranked = scorer.rank_opportunities(opps)
            scores = [scorer.score(o) for o in ranked]
            assert scores == sorted(scores, reverse=True)


class TestMarginOptimizer:
    def test_update_and_get_utilization(self):
        optimizer = MarginOptimizer()
        optimizer.update_margin("polymarket", available=1000.0, used=400.0)

        util = optimizer.get_utilization("polymarket")
        assert util == pytest.approx(0.40, abs=0.01)

    def test_utilization_unknown_platform(self):
        optimizer = MarginOptimizer()
        util = optimizer.get_utilization("unknown")
        assert util == 1.0

    def test_get_best_platform(self):
        with patch("capital_optimizer.MARGIN_EFFICIENCY_ENABLED", True):
            optimizer = MarginOptimizer()
            optimizer.update_margin("polymarket", 1000.0, 800.0)
            optimizer.update_margin("kalshi", 1000.0, 200.0)
            optimizer.update_margin("betfair", 1000.0, 500.0)

            best = optimizer.get_best_platform(["polymarket", "kalshi", "betfair"])
            assert best == "kalshi"

    def test_get_best_platform_disabled(self):
        with patch("capital_optimizer.MARGIN_EFFICIENCY_ENABLED", False):
            optimizer = MarginOptimizer()
            optimizer.update_margin("polymarket", 1000.0, 800.0)
            optimizer.update_margin("kalshi", 1000.0, 200.0)

            best = optimizer.get_best_platform(["polymarket", "kalshi"])
            assert best == "polymarket"

    def test_should_rebalance(self):
        with patch("capital_optimizer.MARGIN_EFFICIENCY_ENABLED", True):
            with patch("capital_optimizer.MARGIN_REBALANCE_THRESHOLD", 0.30):
                optimizer = MarginOptimizer()
                optimizer.update_margin("polymarket", 1000.0, 900.0)
                optimizer.update_margin("kalshi", 1000.0, 100.0)

                transfers = optimizer.should_rebalance()
                assert len(transfers) > 0
                assert any(t[0] == "polymarket" for t in transfers)


class TestTaxOptimizer:
    def test_record_entry_and_get_unrealized_pnl(self):
        optimizer = TaxOptimizer()
        optimizer.record_entry(
            position_id="pos1",
            platform="polymarket",
            cost=100.0,
            quantity=10,
        )

        pnl = optimizer.get_unrealized_pnl("pos1", current_value=120.0)
        assert pnl == pytest.approx(20.0, abs=0.01)

    def test_is_long_term_false_for_new(self):
        optimizer = TaxOptimizer()
        optimizer.record_entry("pos1", "polymarket", 100.0, 10)
        assert optimizer.is_long_term("pos1") is False

    def test_is_long_term_true_for_old(self):
        optimizer = TaxOptimizer()
        old_date = datetime.now() - timedelta(days=400)
        optimizer.record_entry(
            "pos1", "polymarket", 100.0, 10, entry_time=old_date
        )
        assert optimizer.is_long_term("pos1") is True

    def test_get_tax_impact(self):
        with patch("capital_optimizer.TAX_AWARE_ENABLED", True):
            with patch("capital_optimizer.TAX_SHORT_TERM_RATE", 0.35):
                optimizer = TaxOptimizer()
                optimizer.record_entry("pos1", "polymarket", 100.0, 10)

                impact = optimizer.get_tax_impact("pos1", current_value=150.0)
                assert impact == pytest.approx(50.0 * 0.35, abs=0.01)

    def test_get_harvest_candidates(self):
        with patch("capital_optimizer.TAX_AWARE_ENABLED", True):
            with patch("capital_optimizer.TAX_LOSS_HARVEST_THRESHOLD", 0.10):
                with patch("capital_optimizer.TAX_SHORT_TERM_RATE", 0.35):
                    optimizer = TaxOptimizer()
                    optimizer.record_entry("pos1", "polymarket", 100.0, 10)
                    optimizer.record_entry("pos2", "kalshi", 100.0, 10)

                    positions = {"pos1": 80.0, "pos2": 110.0}
                    candidates = optimizer.get_harvest_candidates(positions)

                    assert len(candidates) == 1
                    assert candidates[0][0] == "pos1"
                    assert candidates[0][1] < 0


class TestWithdrawalTimingOptimizer:
    def test_get_withdrawal_delay(self):
        delays = {"polymarket": 2.0, "kalshi": 24.0}
        optimizer = WithdrawalTimingOptimizer(withdrawal_delays=delays)

        assert optimizer.get_withdrawal_delay("polymarket") == 2.0
        assert optimizer.get_withdrawal_delay("kalshi") == 24.0
        assert optimizer.get_withdrawal_delay("unknown") == 24.0

    def test_is_capital_available_in_time(self):
        with patch("capital_optimizer.WITHDRAWAL_TIMING_ENABLED", True):
            delays = {"polymarket": 4.0, "kalshi": 48.0}
            optimizer = WithdrawalTimingOptimizer(withdrawal_delays=delays)

            assert optimizer.is_capital_available_in_time("polymarket", 6.0) is True
            assert optimizer.is_capital_available_in_time("kalshi", 24.0) is False

    def test_get_fastest_source(self):
        delays = {"polymarket": 2.0, "kalshi": 24.0, "betfair": 12.0}
        optimizer = WithdrawalTimingOptimizer(withdrawal_delays=delays)

        fastest = optimizer.get_fastest_source(["polymarket", "kalshi", "betfair"])
        assert fastest == "polymarket"

    def test_score_rebalance(self):
        with patch("capital_optimizer.WITHDRAWAL_TIMING_ENABLED", True):
            delays = {"polymarket": 4.0}
            optimizer = WithdrawalTimingOptimizer(withdrawal_delays=delays)

            score = optimizer.score_rebalance("polymarket", "kalshi", 10.0)
            assert score > 0
            assert score <= 1.0

            score_impossible = optimizer.score_rebalance("polymarket", "kalshi", 2.0)
            assert score_impossible == 0.0


class TestCapitalOptimizer:
    def test_optimize_opportunities_with_cost_scoring(self):
        with patch("capital_optimizer.OPPORTUNITY_COST_SCORING_ENABLED", True):
            optimizer = CapitalOptimizer()
            optimizer.cost_scorer = OpportunityCostScorer(min_annualized_roi=0.0)

            opps = [
                {"net_roi": 0.05, "_days_to_resolution": 365},
                {"net_roi": 0.05, "_days_to_resolution": 30},
            ]

            result = optimizer.optimize_opportunities(opps)
            assert result[0]["_days_to_resolution"] == 30

    def test_get_execution_platform_with_margin(self):
        with patch("capital_optimizer.MARGIN_EFFICIENCY_ENABLED", True):
            optimizer = CapitalOptimizer()
            optimizer.margin_optimizer.update_margin("polymarket", 1000.0, 800.0)
            optimizer.margin_optimizer.update_margin("kalshi", 1000.0, 200.0)

            platform = optimizer.get_execution_platform(
                {},
                ["polymarket", "kalshi"],
            )
            assert platform == "kalshi"


class TestGetCapitalOptimizer:
    def test_returns_singleton(self):
        optimizer1 = get_capital_optimizer()
        optimizer2 = get_capital_optimizer()
        assert optimizer1 is optimizer2
