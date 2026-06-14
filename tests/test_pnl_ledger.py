"""Tests for the unified cross-engine P&L aggregation core."""
from __future__ import annotations

import pytest

from pnl_ledger import (
    PnlEntry,
    PnlError,
    aggregate_pnl,
    clears_hurdle,
)


def _e(engine, lane, bucket, amount):
    return PnlEntry(engine=engine, lane=lane, tax_bucket=bucket, amount_usd=amount, trade_date="2026-06-13")


# ---------------------------------------------------------------------------
# Entry validation (tagging must be correct from trade one)
# ---------------------------------------------------------------------------

def test_rejects_unknown_tax_bucket():
    with pytest.raises(PnlError, match="tax_bucket"):
        _e("arbgrid", "prediction-markets", "capital_gains", 10.0)


def test_requires_engine_and_lane():
    with pytest.raises(PnlError):
        _e("", "prediction-markets", "ordinary", 10.0)
    with pytest.raises(PnlError):
        _e("arbgrid", "", "ordinary", 10.0)


def test_accepts_each_valid_bucket():
    for bucket in ("ordinary", "possible_1256", "gambling"):
        assert _e("arbgrid", "x", bucket, 1.0).tax_bucket == bucket


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def test_rolls_up_by_engine_lane_and_bucket():
    entries = [
        _e("arbgrid", "prediction-markets", "ordinary", 100.0),
        _e("arbgrid", "prediction-markets", "ordinary", -30.0),
        _e("quant", "perp_carry", "possible_1256", 50.0),
        _e("arbgrid", "sports", "gambling", 20.0),
    ]
    s = aggregate_pnl(entries)
    assert s.total_usd == pytest.approx(140.0)
    assert s.by_engine == {"arbgrid": pytest.approx(90.0), "quant": pytest.approx(50.0)}
    assert s.by_lane["prediction-markets"] == pytest.approx(70.0)
    assert s.by_lane["perp_carry"] == pytest.approx(50.0)
    assert s.by_tax_bucket == {
        "ordinary": pytest.approx(70.0),
        "possible_1256": pytest.approx(50.0),
        "gambling": pytest.approx(20.0),
    }


def test_empty_is_zero():
    s = aggregate_pnl([])
    assert s.total_usd == 0.0
    assert s.by_engine == {}


# ---------------------------------------------------------------------------
# Capital-policy hurdle (4.70% LOC floor)
# ---------------------------------------------------------------------------

def test_hurdle_cleared_when_pnl_beats_loc_floor():
    # $10K deployed at the 4.70% LOC rate for a quarter → hurdle ≈ $117.
    cleared, hurdle = clears_hurdle(200.0, 0.047, 10_000.0, 91.25)
    assert hurdle == pytest.approx(117.5, abs=1.0)
    assert cleared is True


def test_hurdle_not_cleared_when_pnl_below_floor():
    cleared, hurdle = clears_hurdle(50.0, 0.047, 10_000.0, 91.25)
    assert cleared is False
    assert hurdle > 50.0


def test_hurdle_false_on_zero_capital():
    assert clears_hurdle(100.0, 0.047, 0.0, 30.0) == (False, 0.0)
