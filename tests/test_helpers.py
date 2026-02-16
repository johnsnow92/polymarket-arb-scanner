"""Tests for scans/helpers.py — capital efficiency scoring."""

import pytest
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scans.helpers import capital_efficiency_score


class TestCapitalEfficiencyScore:
    def test_high_roi_deep_book_scores_highest(self):
        opp = {"net_profit": 0.10, "total_cost": "$0.9000", "_clob_depth": 100.0}
        # ROI = 0.10/0.90 = 11.1%, depth capped at 50 -> score = 0.111 * 50 = ~5.56
        score = capital_efficiency_score(opp)
        assert score == pytest.approx(0.10 / 0.90 * 50, rel=1e-3)

    def test_low_roi_scores_lower(self):
        opp = {"net_profit": 0.005, "total_cost": "$0.9950", "_clob_depth": 100.0}
        # ROI = 0.005/0.995 = 0.50%, depth capped at 50 -> score = ~0.251
        score = capital_efficiency_score(opp)
        assert score == pytest.approx(0.005 / 0.995 * 50, rel=1e-3)

    def test_shallow_book_reduces_score(self):
        opp = {"net_profit": 0.10, "total_cost": "$0.9000", "_clob_depth": 10.0}
        # ROI = 11.1%, depth = 10 (not capped) -> score = 0.111 * 10 = ~1.11
        score = capital_efficiency_score(opp)
        assert score == pytest.approx(0.10 / 0.90 * 10, rel=1e-3)

    def test_depth_capped_at_50(self):
        opp_a = {"net_profit": 0.10, "total_cost": "$0.9000", "_clob_depth": 50.0}
        opp_b = {"net_profit": 0.10, "total_cost": "$0.9000", "_clob_depth": 500.0}
        # Both should score the same since depth caps at 50
        assert capital_efficiency_score(opp_a) == pytest.approx(capital_efficiency_score(opp_b))

    def test_zero_cost_returns_zero(self):
        opp = {"net_profit": 0.10, "total_cost": "$0", "_clob_depth": 100.0}
        assert capital_efficiency_score(opp) == 0.0

    def test_zero_profit_returns_zero(self):
        opp = {"net_profit": 0.0, "total_cost": "$0.9500", "_clob_depth": 100.0}
        assert capital_efficiency_score(opp) == 0.0

    def test_negative_profit_returns_zero(self):
        opp = {"net_profit": -0.01, "total_cost": "$0.9500", "_clob_depth": 100.0}
        assert capital_efficiency_score(opp) == 0.0

    def test_numeric_total_cost(self):
        opp = {"net_profit": 0.05, "total_cost": 0.95, "_clob_depth": 30.0}
        score = capital_efficiency_score(opp)
        assert score == pytest.approx(0.05 / 0.95 * 30, rel=1e-3)

    def test_zero_depth_uses_one(self):
        """Zero depth doesn't zero out the score — ranks by ROI alone."""
        opp = {"net_profit": 0.10, "total_cost": "$0.9000", "_clob_depth": 0}
        score = capital_efficiency_score(opp)
        assert score == pytest.approx(0.10 / 0.90 * 1, rel=1e-3)

    def test_missing_depth_uses_one(self):
        opp = {"net_profit": 0.10, "total_cost": "$0.9000"}
        score = capital_efficiency_score(opp)
        assert score == pytest.approx(0.10 / 0.90 * 1, rel=1e-3)

    def test_ordering_high_roi_deep_beats_low_roi(self):
        """High ROI + deep book should score higher than low ROI + deep book."""
        high_roi = {"net_profit": 0.10, "total_cost": "$0.9000", "_clob_depth": 50.0}
        low_roi = {"net_profit": 0.01, "total_cost": "$0.9900", "_clob_depth": 50.0}
        assert capital_efficiency_score(high_roi) > capital_efficiency_score(low_roi)

    def test_invalid_total_cost_returns_zero(self):
        opp = {"net_profit": 0.10, "total_cost": "invalid", "_clob_depth": 50.0}
        assert capital_efficiency_score(opp) == 0.0
