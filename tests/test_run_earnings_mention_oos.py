"""Tests for the earnings-mention OOS runner (scripts/run_earnings_mention_oos.py).

The deterministic richness/verdict math lives in earnings_mention.py (tested
separately); this pins the runner's own logic: JSON state roundtrip, stale
pending pruning, and the snapshot -> resolve -> accumulate -> verdict wiring
in run_cycle. All network is faked via a duck-typed client — no live Kalshi
calls, matching earnings_mention.py's own FakeClient convention.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from earnings_mention import OosStats, Snapshot
from scripts.run_earnings_mention_oos import (
    _drop_stale_pending,
    _should_alert,
    format_message,
    load_state,
    run_cycle,
    save_state,
)


# --------------------------------------------------------------------------- #
# Fake client (mirrors tests/test_earnings_mention.py's FakeClient)
# --------------------------------------------------------------------------- #
class FakeClient:
    def __init__(self, events=None, settled=None):
        self._events = events or []
        self._settled = settled or {}

    def fetch_all_events(self, *a, **k):
        return self._events

    def get_market_price(self, market):
        return market.get("_yes"), market.get("_no")

    def fetch_market(self, ticker):
        return self._settled.get(ticker)


def _market(ticker, title, close, yes=0.30, no=0.70, **extra):
    m = {
        "ticker": ticker,
        "title": title,
        "close_time": close,
        "_yes": yes,
        "_no": no,
        "volume": extra.pop("volume", 1000),
    }
    m.update(extra)
    return m


NOW = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
CLOSE_12H = "2026-07-06T00:00:00Z"  # 12h after NOW -> inside [close-24h, close-6h]


# --------------------------------------------------------------------------- #
# load_state / save_state
# --------------------------------------------------------------------------- #
class TestState:
    def test_load_state_missing_file_returns_empty(self, tmp_path):
        state = load_state(tmp_path / "does-not-exist.json")
        assert state == {"pending": [], "resolved": [], "last_verdict": "continue"}

    def test_load_state_none_path_returns_empty(self):
        assert load_state(None) == {"pending": [], "resolved": [], "last_verdict": "continue"}

    def test_load_state_corrupt_json_returns_empty(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{not valid json")
        assert load_state(path) == {"pending": [], "resolved": [], "last_verdict": "continue"}

    def test_load_state_non_dict_json_returns_empty(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("[1, 2, 3]")
        assert load_state(path) == {"pending": [], "resolved": [], "last_verdict": "continue"}

    def test_save_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "state.json"
        state = {
            "pending": [{"ticker": "A", "snapshot_ts": NOW.isoformat(), "hours_to_close": 12.0,
                         "yes_price": 0.3, "no_price": 0.7, "volume": 500, "series": "S"}],
            "resolved": [{"ticker": "B", "yes_price": 0.4, "outcome": 1.0, "series": "S",
                          "resolved_ts": NOW.isoformat()}],
            "last_verdict": "continue",
        }
        save_state(path, state)
        assert load_state(path) == state

    def test_save_state_noop_when_path_none(self):
        save_state(None, {"pending": []})  # must not raise


# --------------------------------------------------------------------------- #
# _drop_stale_pending
# --------------------------------------------------------------------------- #
class TestDropStalePending:
    def test_keeps_recent_pending(self):
        snap = Snapshot("A", NOW.isoformat(), 12.0, 0.3, 0.7, 1000, "S")
        assert _drop_stale_pending([snap], NOW) == [snap]

    def test_drops_pending_past_max_age(self):
        old_ts = (NOW - timedelta(days=31)).isoformat()
        snap = Snapshot("A", old_ts, 12.0, 0.3, 0.7, 1000, "S")
        assert _drop_stale_pending([snap], NOW, max_age_days=30.0) == []

    def test_keeps_pending_exactly_at_boundary(self):
        boundary_ts = (NOW - timedelta(days=30)).isoformat()
        snap = Snapshot("A", boundary_ts, 12.0, 0.3, 0.7, 1000, "S")
        assert _drop_stale_pending([snap], NOW, max_age_days=30.0) == [snap]

    def test_unparseable_timestamp_is_kept_not_guessed(self):
        snap = Snapshot("A", "not-a-timestamp", 12.0, 0.3, 0.7, 1000, "S")
        assert _drop_stale_pending([snap], NOW) == [snap]


# --------------------------------------------------------------------------- #
# run_cycle — the core wiring: snapshot -> resolve -> accumulate -> verdict
# --------------------------------------------------------------------------- #
class TestRunCycle:
    def test_fresh_market_is_added_to_pending_not_resolved(self):
        events = [{"markets": [_market("M1", "Will Apple mention AI?", CLOSE_12H, yes=0.30)]}]
        client = FakeClient(events=events)
        result = run_cycle(client, NOW, {"pending": [], "resolved": [], "last_verdict": "continue"})
        assert [p["ticker"] for p in result["pending"]] == ["M1"]
        assert result["resolved"] == []
        assert result["_stats"].n == 0
        assert result["last_verdict"] == "continue"
        assert result["_new_resolved"] == 0
        assert result["_prev_verdict"] == "continue"

    def test_pending_from_prior_run_resolves_and_accumulates_stats(self):
        prior_state = {
            "pending": [{"ticker": "M1", "snapshot_ts": NOW.isoformat(), "hours_to_close": 12.0,
                         "yes_price": 0.30, "no_price": 0.70, "volume": 1000, "series": "S"}],
            "resolved": [],
            "last_verdict": "continue",
        }
        client = FakeClient(events=[], settled={"M1": {"status": "settled", "result": "no"}})
        later = NOW + timedelta(days=7)
        result = run_cycle(client, later, prior_state)
        assert result["pending"] == []
        assert len(result["resolved"]) == 1
        assert result["resolved"][0]["ticker"] == "M1"
        assert result["resolved"][0]["outcome"] == 0.0
        assert result["_new_resolved"] == 1
        # yes_price 0.30, settled NO -> richness = 0.30 - 0.0 = +30pts, n=1 (band 11-50c)
        assert result["_stats"].n == 1
        assert result["_stats"].mean_richness_pts == 30.0

    def test_resolved_history_persists_across_two_cycles(self):
        """Simulates two weekly runs: cycle 1 snapshots, cycle 2 (a week later)
        resolves it and the accumulated resolved log now has that entry."""
        events = [{"markets": [_market("M1", "Will Apple mention AI?", CLOSE_12H, yes=0.30)]}]
        client = FakeClient(events=events)
        state0 = {"pending": [], "resolved": [], "last_verdict": "continue"}
        state1 = run_cycle(client, NOW, state0)
        for k in ("_stats", "_prev_verdict", "_new_resolved"):
            state1.pop(k)
        assert [p["ticker"] for p in state1["pending"]] == ["M1"]

        week_later = NOW + timedelta(days=7)
        client2 = FakeClient(events=[], settled={"M1": {"status": "finalized", "result": "yes"}})
        result2 = run_cycle(client2, week_later, state1)
        assert result2["pending"] == []
        assert [r["ticker"] for r in result2["resolved"]] == ["M1"]
        assert result2["_new_resolved"] == 1

    def test_does_not_resnapshot_a_ticker_already_resolved(self):
        """A ticker already in the resolved log must not be re-added to pending
        even if it still (implausibly) shows up in a fresh events fetch."""
        events = [{"markets": [_market("M1", "Will Apple mention AI?", CLOSE_12H, yes=0.30)]}]
        client = FakeClient(events=events)
        state = {
            "pending": [],
            "resolved": [{"ticker": "M1", "yes_price": 0.30, "outcome": 1.0, "series": "S",
                          "resolved_ts": NOW.isoformat()}],
            "last_verdict": "continue",
        }
        result = run_cycle(client, NOW, state)
        assert result["pending"] == []
        assert len(result["resolved"]) == 1

    def test_drops_stale_pending_that_never_settles(self):
        old_ts = (NOW - timedelta(days=45)).isoformat()
        state = {
            "pending": [{"ticker": "VOID1", "snapshot_ts": old_ts, "hours_to_close": 12.0,
                         "yes_price": 0.30, "no_price": 0.70, "volume": 1000, "series": "S"}],
            "resolved": [],
            "last_verdict": "continue",
        }
        client = FakeClient(events=[], settled={})  # VOID1 never resolves
        result = run_cycle(client, NOW, state)
        assert result["pending"] == []
        assert result["resolved"] == []

    def test_verdict_flip_is_visible_via_prev_verdict(self):
        """_prev_verdict carries the state's last_verdict through untouched so
        callers can detect continue -> pursue/kill transitions."""
        state = {"pending": [], "resolved": [], "last_verdict": "pursue"}
        client = FakeClient(events=[])
        result = run_cycle(client, NOW, state)
        assert result["_prev_verdict"] == "pursue"
        assert result["last_verdict"] == "continue"  # n=0 -> always continue


# --------------------------------------------------------------------------- #
# _should_alert
# --------------------------------------------------------------------------- #
class TestShouldAlert:
    def test_alerts_on_new_resolution(self):
        assert _should_alert(1, "continue", "continue", False) is True

    def test_alerts_on_verdict_change(self):
        assert _should_alert(0, "continue", "pursue", False) is True

    def test_alerts_when_forced(self):
        assert _should_alert(0, "continue", "continue", True) is True

    def test_no_alert_when_nothing_changed(self):
        assert _should_alert(0, "continue", "continue", False) is False


# --------------------------------------------------------------------------- #
# format_message
# --------------------------------------------------------------------------- #
class TestFormatMessage:
    def test_includes_core_fields(self):
        stats = OosStats(n=120, mean_richness_pts=10.3, z=2.41, by_category={"KXEARNINGSMENTION": (120, 10.3)})
        msg = format_message(stats, "pursue", new_resolved=5)
        assert "n=120" in msg
        assert "PURSUE" in msg
        assert "New settlements resolved this run: 5" in msg
        assert "KXEARNINGSMENTION" in msg

    def test_handles_empty_by_category(self):
        stats = OosStats(n=0, mean_richness_pts=0.0, z=0.0, by_category={})
        msg = format_message(stats, "continue", new_resolved=0)
        assert "CONTINUE" in msg
        assert "n=0" in msg
