"""Tests for the fallen-angel IG→HY rating-crossover classifier."""
from __future__ import annotations

from fallen_angel import (
    RatingChange,
    classify_fallen_angel,
    is_fallen_angel,
    is_investment_grade,
    is_rising_star,
    rating_rank,
    scan_rating_changes,
)


def _chg(from_r, to_r, issuer="Acme Corp"):
    return RatingChange(issuer=issuer, from_rating=from_r, to_rating=to_r, agency="S&P")


# ---------------------------------------------------------------------------
# IG boundary
# ---------------------------------------------------------------------------

def test_investment_grade_boundary_sp_fitch():
    assert is_investment_grade("BBB-") is True     # lowest IG
    assert is_investment_grade("BB+") is False     # highest HY
    assert is_investment_grade("AAA") is True


def test_investment_grade_boundary_moodys():
    assert is_investment_grade("Baa3") is True      # lowest IG
    assert is_investment_grade("Ba1") is False      # highest HY


def test_unknown_rating_is_not_ig():
    assert is_investment_grade("ZZZ") is False
    assert rating_rank("ZZZ") is None


# ---------------------------------------------------------------------------
# Fallen-angel crossover
# ---------------------------------------------------------------------------

def test_fallen_angel_sp_crossover():
    assert is_fallen_angel(_chg("BBB-", "BB+")) is True


def test_fallen_angel_moodys_crossover():
    assert is_fallen_angel(_chg("Baa2", "Ba1")) is True


def test_downgrade_within_ig_is_not_fallen_angel():
    assert is_fallen_angel(_chg("A", "BBB")) is False


def test_downgrade_within_hy_is_not_fallen_angel():
    assert is_fallen_angel(_chg("BB", "B")) is False


def test_upgrade_is_not_fallen_angel():
    assert is_fallen_angel(_chg("BBB", "BBB+")) is False


def test_rising_star_is_hy_to_ig():
    assert is_rising_star(_chg("BB+", "BBB-")) is True
    assert is_fallen_angel(_chg("BB+", "BBB-")) is False


def test_unknown_rating_is_not_a_crossover():
    assert is_fallen_angel(_chg("BBB-", "ZZZ")) is False


# ---------------------------------------------------------------------------
# Classification + batch
# ---------------------------------------------------------------------------

def test_classify_records_notches():
    event = classify_fallen_angel(_chg("BBB", "BB-"))  # BBB(8) -> BB-(12), 4 notches
    assert event is not None
    assert event.notches == 4
    assert "IG→HY" in event.note


def test_scan_keeps_only_fallen_angels():
    changes = [
        _chg("BBB-", "BB+", issuer="A"),   # fallen angel
        _chg("A", "BBB", issuer="B"),       # within IG
        _chg("Baa3", "Ba1", issuer="C"),    # fallen angel (Moody's)
        _chg("BB", "B", issuer="D"),        # within HY
    ]
    events = scan_rating_changes(changes)
    assert [e.issuer for e in events] == ["A", "C"]
