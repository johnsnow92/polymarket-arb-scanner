"""Venue-legality hard gate — off-allowlist orders = 0, naked-leg events = 0.

Covers the pure detector `_off_allowlist_venues`, which ArbitrageExecutor.execute
uses to veto a whole opportunity atomically before any leg fills — replacing the
per-leg skip that could strand an already-filled leg as a naked position.
"""
from __future__ import annotations

from executor import _off_allowlist_venues


def test_all_legs_enabled_returns_empty():
    legs = [{"platform": "kalshi"}, {"platform": "kalshi"}]
    assert _off_allowlist_venues(legs, frozenset({"kalshi"})) == []


def test_single_off_allowlist_leg_detected():
    """A Polymarket leg is flagged when Polymarket is not execution-enabled."""
    legs = [{"platform": "kalshi"}, {"platform": "polymarket"}]
    assert _off_allowlist_venues(legs, frozenset({"kalshi"})) == ["polymarket"]


def test_multiple_blocked_sorted_and_deduped():
    legs = [
        {"platform": "sxbet"},
        {"platform": "betfair"},
        {"platform": "sxbet"},
        {"platform": "kalshi"},
    ]
    assert _off_allowlist_venues(legs, frozenset({"kalshi"})) == ["betfair", "sxbet"]


def test_missing_or_empty_platform_ignored():
    legs = [{"platform": "kalshi"}, {"side": "yes"}, {"platform": ""}]
    assert _off_allowlist_venues(legs, frozenset({"kalshi"})) == []


def test_forecastex_allowlisted_when_enabled():
    legs = [{"platform": "kalshi"}, {"platform": "ibkr"}]
    assert _off_allowlist_venues(legs, frozenset({"kalshi", "ibkr"})) == []


def test_cross_venue_with_one_blocked_leg_flags_only_the_blocked():
    """A Polymarket×Kalshi arb with Polymarket disabled flags Polymarket only —
    the whole opportunity is then vetoed, so the Kalshi leg never fills alone."""
    legs = [{"platform": "kalshi"}, {"platform": "polymarket"}]
    blocked = _off_allowlist_venues(legs, frozenset({"kalshi"}))
    assert blocked == ["polymarket"]
    assert bool(blocked) is True  # truthy → execute() vetoes the opportunity
