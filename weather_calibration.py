"""Weather calibration math — empirical-CDF shift derivation + PIT gate.

Fills the ``derive_shift`` seam flagged in the Plan 11 draft spec (Open
Question #1): ``weather_paper_rules.calibrate(raw_prob, shift)`` expects a
scalar shift in PROBABILITY space, but the NBM error archive is naturally in
DEGREE space (forecast MaxT - actual MaxT). This module performs the
conversion via empirical-CDF percentile interpolation, and provides the
PIT-uniformity diagnostic that gates whether the calibration may be used
at all.

Semantics
---------
With signed errors ``e = forecast_max_f - actual_max_f`` (positive = NBM ran
hot) and ``d = forecast_max_f - bracket_threshold_f`` (the threshold
distance, positive = forecast above the bracket threshold):

    actual > threshold  <=>  e < d

so the calibrated probability that the actual MaxT clears the threshold is
the empirical CDF of historical errors evaluated at ``d``. ``derive_shift``
returns ``F_hat(d) - raw_prob`` so that
``calibrate(raw_prob, shift) == F_hat(d)`` exactly (already in [0, 1], so
calibrate()'s clamp is a no-op).

Empirical CDF construction: Hazen plotting positions ``(i - 0.5) / n`` on the
sorted sample, linear interpolation between order statistics. Tied values are
collapsed to a single knot at the mean plotting position of the tied block
(the mid-distribution CDF), keeping the interpolant a well-defined function.
Outside the sample range the CDF is clamped to ``0.5 / n`` / ``1 - 0.5 / n``
— never exactly 0 or 1, so no finite sample can claim certainty.

Fail-closed edges: fewer than ``min_samples`` errors, non-finite inputs, or
``raw_prob`` outside [0, 1] raise ``ValueError``. Callers must not fall back
to an uncalibrated path silently.

PIT gate: ``pit_uniformity`` runs a one-sample Kolmogorov-Smirnov test of the
probability-integral-transform values against Uniform(0, 1), using the
asymptotic critical value ``D_crit = 1.3581 / sqrt(n)`` at alpha = 0.05
(Smirnov's asymptotic formula — adequate for the n >= 30 samples this gate
requires; exact small-sample tables are NOT used, which is why small n
fails closed instead). ``PITResult.passed`` is a hard gate: when it is
False, ``derive_shift`` output for that city/window must not be used.

Pure + deterministic, stdlib only.
"""
from __future__ import annotations

import bisect
import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum error-archive size before a shift may be derived. Below this the
# empirical CDF is too coarse to interpolate meaningfully — fail closed.
MIN_ERROR_SAMPLES = 30

# Minimum PIT sample size for the asymptotic KS critical value to be a fair
# approximation. Below this the gate fails closed (passed=False).
MIN_PIT_SAMPLES = 30

# Smirnov asymptotic critical-value coefficient at alpha = 0.05:
# D_crit = c(alpha) / sqrt(n), c(0.05) = sqrt(-0.5 * ln(0.05 / 2)) ~= 1.3581.
KS_ALPHA = 0.05
_KS_COEFF_05 = math.sqrt(-0.5 * math.log(KS_ALPHA / 2.0))


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PITResult:
    """Outcome of the PIT-uniformity gate."""

    statistic: float   # KS D statistic vs Uniform(0, 1); NaN when n too small
    threshold: float   # critical value the statistic is compared against
    n: int             # number of PIT values tested
    passed: bool       # hard gate: False => reject the calibration
    reason: str        # human-readable explanation of the verdict


# ---------------------------------------------------------------------------
# Empirical CDF (percentile interpolation)
# ---------------------------------------------------------------------------


def empirical_cdf(errors_degrees: list[float], x: float,
                  min_samples: int = MIN_ERROR_SAMPLES) -> float:
    """Empirical CDF of the error sample evaluated at ``x``.

    Hazen plotting positions with linear interpolation between order
    statistics; ties collapsed to their mean plotting position; clamped to
    [0.5/n, 1 - 0.5/n] outside the sample range.

    Args:
        errors_degrees: Historical error sample in degree space
            (forecast MaxT - actual MaxT).
        x: Point at which to evaluate the empirical CDF (degree space).
        min_samples: Minimum sample size before evaluation is allowed
            (must be >= 1; defaults to ``MIN_ERROR_SAMPLES``).

    Returns:
        The interpolated empirical CDF value in (0, 1) — never exactly
        0 or 1 for a finite sample.

    Raises:
        ValueError: If ``min_samples`` < 1, the sample is shorter than
            ``min_samples``, or ``x`` / any sample value is non-finite
            (fail-closed).
    """
    if min_samples < 1:
        raise ValueError(
            "empirical_cdf: min_samples must be >= 1, got %d" % min_samples)
    if len(errors_degrees) < min_samples:
        raise ValueError(
            "empirical_cdf: %d error samples < required minimum %d (fail-closed)"
            % (len(errors_degrees), min_samples))
    if not math.isfinite(x):
        raise ValueError("empirical_cdf: x must be finite, got %r" % (x,))
    for e in errors_degrees:
        if not math.isfinite(e):
            raise ValueError("empirical_cdf: non-finite error sample %r" % (e,))

    n = len(errors_degrees)
    ordered = sorted(errors_degrees)

    # Knots: unique values with the mean Hazen position of each tied block.
    xs: list[float] = []
    ps: list[float] = []
    i = 0
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1] == ordered[i]:
            j += 1
        # positions i..j (0-based) -> Hazen (k + 0.5) / n; mean over the block
        mean_pos = ((i + j) / 2.0 + 0.5) / n
        xs.append(ordered[i])
        ps.append(mean_pos)
        i = j + 1

    if x <= xs[0]:
        return ps[0] if x == xs[0] else 0.5 / n
    if x >= xs[-1]:
        return ps[-1] if x == xs[-1] else 1.0 - 0.5 / n

    k = bisect.bisect_right(xs, x)
    x_lo, x_hi = xs[k - 1], xs[k]
    p_lo, p_hi = ps[k - 1], ps[k]
    span = x_hi - x_lo
    if math.isinf(span):
        # Finite endpoints whose difference overflows float range: rescale by
        # the largest magnitude so the interpolation fraction stays finite.
        scale = max(abs(x), abs(x_lo), abs(x_hi))
        logger.debug("empirical_cdf: rescaling interpolation span by %g to avoid overflow", scale)
        x, x_lo, x_hi = x / scale, x_lo / scale, x_hi / scale
        span = x_hi - x_lo
    return p_lo + (p_hi - p_lo) * (x - x_lo) / span


# ---------------------------------------------------------------------------
# Shift derivation (degree space -> probability space)
# ---------------------------------------------------------------------------


def derive_shift(errors_degrees: list[float], raw_prob: float,
                 threshold_distance_degrees: float,
                 min_samples: int = MIN_ERROR_SAMPLES) -> float:
    """Probability-space shift for ``weather_paper_rules.calibrate``.

    Args:
        errors_degrees: Historical NBM errors in degree space
            (forecast MaxT - actual MaxT).
        raw_prob: Uncalibrated model probability of the YES outcome
            (actual MaxT above the bracket threshold), in [0, 1].
        threshold_distance_degrees: Forecast MaxT - bracket threshold.
        min_samples: Minimum error-archive size before a shift may be
            derived (must be >= 1; defaults to ``MIN_ERROR_SAMPLES``).

    Returns:
        ``shift`` such that ``calibrate(raw_prob, shift)`` equals the
        empirical-CDF calibrated probability F_hat(threshold_distance).

    Raises:
        ValueError: If ``raw_prob`` is outside [0, 1], the sample is too
            short, ``min_samples`` < 1, or any input is non-finite —
            callers must treat this as "no calibration available", not
            shift = 0 (fail-closed).
    """
    if not math.isfinite(raw_prob) or not 0.0 <= raw_prob <= 1.0:
        raise ValueError("derive_shift: raw_prob must be in [0, 1], got %r" % (raw_prob,))
    calibrated = empirical_cdf(errors_degrees, threshold_distance_degrees,
                               min_samples=min_samples)
    return calibrated - raw_prob


# ---------------------------------------------------------------------------
# PIT-uniformity gate
# ---------------------------------------------------------------------------


def pit_uniformity(pit_values: list[float],
                   min_samples: int = MIN_PIT_SAMPLES) -> PITResult:
    """Kolmogorov-Smirnov test of PIT values against Uniform(0, 1).

    ``pit_values`` are F_hat(observed error) over held-out (forecast,
    outcome) pairs; a well-calibrated CDF makes them ~Uniform(0, 1).

    Args:
        pit_values: Probability-integral-transform values, each in [0, 1].
        min_samples: Minimum sample size for the asymptotic KS critical
            value to be a fair approximation (must be >= 1; defaults to
            ``MIN_PIT_SAMPLES``). Below it the gate fails closed.

    Returns:
        A ``PITResult``; ``passed`` is True iff n >= ``min_samples`` and
        D < 1.3581 / sqrt(n) (asymptotic alpha = 0.05 critical value).
        Anything else — including tiny samples — fails closed.

    Raises:
        ValueError: If ``min_samples`` < 1 or any PIT value is outside
            [0, 1] or non-finite.
    """
    if min_samples < 1:
        raise ValueError(
            "pit_uniformity: min_samples must be >= 1, got %d" % min_samples)
    n = len(pit_values)
    for v in pit_values:
        if not math.isfinite(v) or not 0.0 <= v <= 1.0:
            raise ValueError("pit_uniformity: PIT value out of [0, 1]: %r" % (v,))

    if n < min_samples:
        return PITResult(
            statistic=float("nan"), threshold=float("nan"), n=n, passed=False,
            reason="fail-closed: %d PIT values < required minimum %d" % (n, min_samples))

    ordered = sorted(pit_values)
    d_stat = 0.0
    for i, u in enumerate(ordered):
        d_plus = (i + 1) / n - u
        d_minus = u - i / n
        d_stat = max(d_stat, d_plus, d_minus)

    threshold = _KS_COEFF_05 / math.sqrt(n)
    passed = d_stat < threshold
    reason = ("KS D=%.4f %s critical %.4f (asymptotic, alpha=%.2f, n=%d)"
              % (d_stat, "<" if passed else ">=", threshold, KS_ALPHA, n))
    return PITResult(statistic=d_stat, threshold=threshold, n=n,
                     passed=passed, reason=reason)
