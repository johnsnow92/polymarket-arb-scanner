"""Tests for calibration_tracker.py — Strategy #41 Platform Calibration."""

import pytest
from unittest.mock import patch, MagicMock
import sys
import os
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from calibration_tracker import (
    CalibrationTracker,
    get_calibration_tracker,
)


class TestCalibrationTracker:
    @pytest.fixture
    def tracker(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
            db_path = f.name
        yield CalibrationTracker(db_path=db_path)
        try:
            os.unlink(db_path)
        except Exception:
            pass

    def test_record_resolution(self, tracker):
        tracker.record_resolution(
            platform="polymarket",
            market_key="m1",
            prediction=0.70,
            outcome=1,
        )

    def test_get_brier_score_insufficient_data(self, tracker):
        tracker.record_resolution("polymarket", "m1", 0.70, 1)
        brier = tracker.get_platform_brier_score("polymarket")
        assert brier is None

    def test_get_brier_score_with_data(self, tracker):
        for i in range(15):
            prediction = 0.70
            outcome = 1 if i % 3 != 0 else 0
            tracker.record_resolution("polymarket", f"m{i}", prediction, outcome)

        brier = tracker.get_platform_brier_score("polymarket")
        assert brier is not None
        assert 0.0 <= brier <= 1.0

    def test_get_calibration_error_insufficient_data(self, tracker):
        for i in range(10):
            tracker.record_resolution("polymarket", f"m{i}", 0.50, 1)

        cal_error = tracker.get_platform_calibration_error("polymarket")
        assert cal_error is None

    def test_get_weight_multiplier_no_data(self, tracker):
        weight = tracker.get_weight_multiplier("unknown_platform")
        assert weight == 1.0

    def test_get_weight_multiplier_with_data(self, tracker):
        with patch("calibration_tracker.CALIBRATION_WEIGHTING_ENABLED", True):
            for i in range(15):
                prediction = 0.80
                outcome = 1 if i % 5 != 0 else 0
                tracker.record_resolution("metaculus", f"m{i}", prediction, outcome)

            weight = tracker.get_weight_multiplier("metaculus")
            assert weight >= 0.5
            assert weight <= 2.0

    def test_get_weight_multiplier_disabled(self, tracker):
        with patch("calibration_tracker.CALIBRATION_WEIGHTING_ENABLED", False):
            weight = tracker.get_weight_multiplier("polymarket", base_weight=1.5)
            assert weight == 1.5

    def test_get_all_platform_stats(self, tracker):
        for i in range(15):
            tracker.record_resolution("polymarket", f"pm{i}", 0.60, 1)
            tracker.record_resolution("kalshi", f"km{i}", 0.40, 0)

        stats = tracker.get_all_platform_stats()
        assert "polymarket" in stats
        assert "kalshi" in stats

    def test_adjust_consensus_weights_disabled(self, tracker):
        with patch("calibration_tracker.CALIBRATION_WEIGHTING_ENABLED", False):
            probs = {"polymarket": 0.60, "kalshi": 0.50}
            weights = tracker.adjust_consensus_weights(probs)

            assert weights["polymarket"] == pytest.approx(0.5, abs=0.01)
            assert weights["kalshi"] == pytest.approx(0.5, abs=0.01)

    def test_calculate_weighted_consensus(self, tracker):
        with patch("calibration_tracker.CALIBRATION_WEIGHTING_ENABLED", False):
            probs = {"polymarket": 0.60, "kalshi": 0.40}
            consensus = tracker.calculate_weighted_consensus(probs)

            assert consensus == pytest.approx(0.50, abs=0.01)

    def test_cache_invalidation_on_record(self, tracker):
        with patch("calibration_tracker.CALIBRATION_WEIGHTING_ENABLED", True):
            for i in range(15):
                tracker.record_resolution("polymarket", f"m{i}", 0.70, 1)

            tracker.get_weight_multiplier("polymarket")
            assert "polymarket:all" in tracker._in_memory_cache

            tracker.record_resolution("polymarket", "m_new", 0.80, 0)
            assert "polymarket:all" not in tracker._in_memory_cache


class TestGetCalibrationTracker:
    def test_returns_instance(self):
        tracker = get_calibration_tracker()
        assert isinstance(tracker, CalibrationTracker)
