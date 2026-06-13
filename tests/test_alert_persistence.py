"""Durable alert persistence → ops_alerts view + hard-guardrail KPI counts.

The in-memory ring buffer is per-process; a daily digest cron needs alerts in the
DB. AlertManager persists to a TradeDB when one is attached, and db.guardrail_kpi_counts
counts the hard-guardrail events (off-allowlist attempts, naked-leg events) in a window.
"""
from __future__ import annotations

from alerting import AlertManager
from db import TradeDB


def test_log_alert_persists_and_queryable():
    db = TradeDB(":memory:")
    rid = db.log_alert("PARTIAL_FILL", "CRITICAL", "naked exposure", details='{"x": 1}')
    assert rid is not None
    rows = db.get_alerts_since(0)
    assert len(rows) == 1
    assert rows[0]["alert_type"] == "PARTIAL_FILL"
    assert rows[0]["severity"] == "CRITICAL"
    assert rows[0]["message"] == "naked exposure"


def test_log_alert_never_raises_on_bad_db():
    """Best-effort: a broken connection returns None, not an exception (alerting must
    never break or recurse on a persistence failure)."""
    db = TradeDB(":memory:")
    db.conn.close()  # force the write to fail
    assert db.log_alert("RATE_LIMIT", "WARNING", "429") is None


def test_guardrail_kpi_counts():
    db = TradeDB(":memory:")
    db.log_alert("OFF_ALLOWLIST", "CRITICAL", "vetoed")
    db.log_alert("OFF_ALLOWLIST", "CRITICAL", "vetoed again")
    db.log_alert("PARTIAL_FILL", "CRITICAL", "naked")
    db.log_alert("RATE_LIMIT", "WARNING", "429")
    db.log_alert("DB_WRITE_FAILURE", "CRITICAL", "disk")
    counts = db.guardrail_kpi_counts(window_hours=24)
    assert counts["off_allowlist_attempts"] == 2
    assert counts["naked_leg_events"] == 1
    assert counts["rate_limits"] == 1
    assert counts["db_write_failures"] == 1
    assert counts["total"] == 5


def test_guardrail_kpi_window_excludes_old_events():
    db = TradeDB(":memory:")
    db.log_alert("OFF_ALLOWLIST", "CRITICAL", "ancient", epoch=1.0)  # ~1970
    counts = db.guardrail_kpi_counts(window_hours=24)
    assert counts["off_allowlist_attempts"] == 0
    assert counts["total"] == 0


def test_alertmanager_persists_when_db_attached():
    db = TradeDB(":memory:")
    mgr = AlertManager(notifier=None, rate_limit_seconds=0, db=db)
    mgr.alert_off_allowlist("polymarket", opp_type="Cross", market="X")
    mgr.alert_partial_fill("Binary", filled_legs=1, total_legs=2)
    counts = db.guardrail_kpi_counts(window_hours=24)
    assert counts["off_allowlist_attempts"] == 1
    assert counts["naked_leg_events"] == 1


def test_alertmanager_set_db_after_construction():
    db = TradeDB(":memory:")
    mgr = AlertManager(notifier=None, rate_limit_seconds=0)  # no db initially
    mgr.alert_rate_limit("kalshi")          # not persisted (no db yet)
    mgr.set_db(db)
    mgr.alert_rate_limit("kalshi")          # persisted now
    assert db.guardrail_kpi_counts(window_hours=24)["rate_limits"] == 1


def test_alertmanager_without_db_does_not_error():
    mgr = AlertManager(notifier=None, rate_limit_seconds=0)  # no db — backward compatible
    assert mgr.alert_partial_fill("Binary", 1, 2) is True
