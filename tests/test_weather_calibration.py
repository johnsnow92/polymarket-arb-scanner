"""Tests for weather_calibration — empirical-CDF shift derivation + PIT gate."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from weather_calibration import (  # noqa: E402
    MIN_ERROR_SAMPLES,
    MIN_PIT_SAMPLES,
    PITResult,
    derive_shift,
    empirical_cdf,
    pit_uniformity,
)
from weather_paper_rules import calibrate  # noqa: E402

# Symmetric 5-point sample: Hazen positions 0.1, 0.3, 0.5, 0.7, 0.9
FIVE = [-2.0, -1.0, 0.0, 1.0, 2.0]


class TestEmpiricalCdf:
    def test_hand_computed_at_order_statistics(self):
        assert empirical_cdf(FIVE, -2.0, min_samples=1) == pytest.approx(0.1)
        assert empirical_cdf(FIVE, 0.0, min_samples=1) == pytest.approx(0.5)
        assert empirical_cdf(FIVE, 2.0, min_samples=1) == pytest.approx(0.9)

    def test_linear_interpolation_between_order_statistics(self):
        assert empirical_cdf(FIVE, 0.5, min_samples=1) == pytest.approx(0.6)
        assert empirical_cdf(FIVE, 0.25, min_samples=1) == pytest.approx(0.55)
        assert empirical_cdf(FIVE, 1.5, min_samples=1) == pytest.approx(0.8)

    def test_edges_clamped_never_zero_or_one(self):
        assert empirical_cdf(FIVE, -100.0, min_samples=1) == pytest.approx(0.1)
        assert empirical_cdf(FIVE, 100.0, min_samples=1) == pytest.approx(0.9)

    def test_ties_collapse_to_mean_plotting_position(self):
        tied = [0.0, 0.0, 0.0, 0.0, 1.0]
        # tied block occupies positions 0..3 -> mean Hazen (1.5 + 0.5) / 5 = 0.4
        assert empirical_cdf(tied, 0.0, min_samples=1) == pytest.approx(0.4)
        assert empirical_cdf(tied, 1.0, min_samples=1) == pytest.approx(0.9)
        assert empirical_cdf(tied, 0.5, min_samples=1) == pytest.approx(0.65)

    def test_short_sample_fails_closed(self):
        with pytest.raises(ValueError):
            empirical_cdf(FIVE, 0.0)  # default min_samples = 30
        with pytest.raises(ValueError):
            empirical_cdf([], 0.0, min_samples=1)

    def test_non_finite_inputs_rejected(self):
        with pytest.raises(ValueError):
            empirical_cdf(FIVE, float("nan"), min_samples=1)
        with pytest.raises(ValueError):
            empirical_cdf([0.0, float("inf"), 1.0], 0.0, min_samples=1)


class TestDeriveShift:
    def test_hand_computed_shift(self):
        # F(0) = 0.5; raw 0.4 -> shift +0.1
        assert derive_shift(FIVE, 0.4, 0.0, min_samples=1) == pytest.approx(0.1)
        # F(1) = 0.7; raw 0.7 -> shift 0
        assert derive_shift(FIVE, 0.7, 1.0, min_samples=1) == pytest.approx(0.0)
        # F(0.5) = 0.6; raw 0.9 -> shift -0.3
        assert derive_shift(FIVE, 0.9, 0.5, min_samples=1) == pytest.approx(-0.3)

    def test_hot_biased_model_shifted_down(self):
        # NBM ran hot in every historical sample (errors all positive):
        # forecast sitting exactly at the threshold should be pushed near 0.
        hot = [float(i) for i in range(1, 31)]  # n = 30, meets default minimum
        shift = derive_shift(hot, 0.5, 0.0)
        assert calibrate(0.5, shift) == pytest.approx(0.5 / 30)
        assert shift < 0

    def test_raw_prob_out_of_range_rejected(self):
        for bad in (-0.01, 1.01, float("nan")):
            with pytest.raises(ValueError):
                derive_shift(FIVE, bad, 0.0, min_samples=1)

    def test_short_sample_fails_closed(self):
        with pytest.raises(ValueError):
            derive_shift(FIVE, 0.5, 0.0)  # 5 < MIN_ERROR_SAMPLES
        assert MIN_ERROR_SAMPLES == 30

    def test_calibrate_output_stays_in_unit_interval(self):
        # Property: for any raw_prob/threshold grid, calibrate(raw, shift)
        # equals the empirical-CDF value and stays within [0, 1].
        errors = [(-1) ** i * (i * 0.37 % 4.0) for i in range(60)]
        for raw in (0.0, 0.1, 0.5, 0.9, 1.0):
            for dist in (-6.0, -1.5, 0.0, 0.7, 2.2, 8.0):
                shift = derive_shift(errors, raw, dist)
                out = calibrate(raw, shift)
                assert 0.0 <= out <= 1.0
                assert out == pytest.approx(empirical_cdf(errors, dist))

    def test_determinism(self):
        errors = [math.sin(i) * 3 for i in range(50)]
        a = derive_shift(list(errors), 0.42, 1.3)
        b = derive_shift(list(errors), 0.42, 1.3)
        assert a == b
        assert empirical_cdf(errors, 0.9) == empirical_cdf(errors, 0.9)


class TestPitUniformity:
    def test_uniform_grid_passes(self):
        n = 100
        pit = [(i + 0.5) / n for i in range(n)]
        res = pit_uniformity(pit)
        assert isinstance(res, PITResult)
        assert res.passed is True
        assert res.n == n
        # exact D for the mid-grid is 0.5 / n
        assert res.statistic == pytest.approx(0.5 / n)
        assert res.statistic < res.threshold

    def test_clustered_values_fail(self):
        res = pit_uniformity([0.5] * 100)
        assert res.passed is False
        assert res.statistic == pytest.approx(0.5)

    def test_skewed_values_fail(self):
        # everything piled in the bottom quartile
        res = pit_uniformity([(i + 0.5) / 400 for i in range(100)])
        assert res.passed is False

    def test_tiny_n_fails_closed(self):
        res = pit_uniformity([0.1, 0.5, 0.9])
        assert res.passed is False
        assert res.n == 3
        assert math.isnan(res.statistic)
        assert "fail-closed" in res.reason
        assert MIN_PIT_SAMPLES == 30

    def test_out_of_range_values_rejected(self):
        with pytest.raises(ValueError):
            pit_uniformity([0.5] * 29 + [1.5])
        with pytest.raises(ValueError):
            pit_uniformity([float("nan")] * 30)

    def test_threshold_is_asymptotic_formula(self):
        res = pit_uniformity([(i + 0.5) / 50 for i in range(50)])
        assert res.threshold == pytest.approx(
            math.sqrt(-0.5 * math.log(0.025)) / math.sqrt(50))

    def test_determinism(self):
        pit = [((i * 7919) % 100 + 0.5) / 100 for i in range(100)]
        assert pit_uniformity(list(pit)) == pit_uniformity(list(pit))
