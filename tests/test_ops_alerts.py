"""Ops-alert types + typed helpers: 429 rate-limit, partial-fill, DB-write-failure,
heartbeat. These are the Week-1 observability rails routed to ClaudeClaw.
"""
from __future__ import annotations

import pytest

from alerting import AlertManager, AlertType, Severity


def _mgr(rate_limit_seconds: float = 0.0) -> AlertManager:
    # notifier=None → logging-only; alerts still recorded in the ring buffer.
    return AlertManager(notifier=None, rate_limit_seconds=rate_limit_seconds)


def _last(mgr: AlertManager) -> dict:
    return list(mgr._recent_alerts)[-1]


def test_all_four_ops_types_exist():
    for name in ("RATE_LIMIT", "PARTIAL_FILL", "DB_WRITE_FAILURE", "HEARTBEAT"):
        assert hasattr(AlertType, name)


def test_rate_limit_alert_fires_warning():
    mgr = _mgr()
    assert mgr.alert_rate_limit("kalshi", endpoint="/orders", retry_after=2.0) is True
    rec = _last(mgr)
    assert AlertType.RATE_LIMIT.value in str(rec["type"])
    assert Severity.WARNING.value in str(rec["severity"])
    assert "kalshi" in rec["message"]
    assert "429" in rec["message"]


def test_partial_fill_alert_is_critical():
    """The naked-leg detector must be CRITICAL and name the fill ratio."""
    mgr = _mgr()
    assert mgr.alert_partial_fill("Binary", filled_legs=1, total_legs=2, market="X") is True
    rec = _last(mgr)
    assert AlertType.PARTIAL_FILL.value in str(rec["type"])
    assert Severity.CRITICAL.value in str(rec["severity"])
    assert "1/2" in rec["message"]
    assert "naked" in rec["message"].lower()


def test_db_write_failure_alert_is_critical():
    mgr = _mgr()
    assert mgr.alert_db_write_failure("log_trade", RuntimeError("disk full")) is True
    rec = _last(mgr)
    assert AlertType.DB_WRITE_FAILURE.value in str(rec["type"])
    assert Severity.CRITICAL.value in str(rec["severity"])
    assert "log_trade" in rec["message"]


def test_heartbeat_alert_is_info():
    mgr = _mgr()
    assert mgr.heartbeat("continuous") is True
    rec = _last(mgr)
    assert AlertType.HEARTBEAT.value in str(rec["type"])
    assert Severity.INFO.value in str(rec["severity"])
    assert "continuous" in rec["message"]


def test_heartbeat_is_rate_limited_within_window():
    """A dead process is detected by the ABSENCE of heartbeats, so heartbeats are
    rate-limited like any alert: the second within the window is suppressed."""
    mgr = _mgr(rate_limit_seconds=300)
    assert mgr.heartbeat() is True
    assert mgr.heartbeat() is False


def test_helpers_record_detail_context():
    mgr = _mgr()
    mgr.alert_partial_fill("Cross", filled_legs=1, total_legs=3, market="ETH")
    rec = _last(mgr)
    details = rec.get("details") or {}
    assert details.get("filled_legs") == 1
    assert details.get("total_legs") == 3


def test_db_write_failure_fires_alert_and_reraises(monkeypatch):
    """A failed commit on a critical write (db.py) fires DB_WRITE_FAILURE via the
    lazily-imported alert_manager and re-raises so the caller still knows."""
    import sqlite3

    import alerting
    from db import TradeDB

    fired = []

    class _FakeAM:
        def alert_db_write_failure(self, operation, error):
            fired.append((operation, str(error)))
            return True

    monkeypatch.setattr(alerting, "alert_manager", _FakeAM())
    db = TradeDB(":memory:")

    real_conn = db.conn

    class _FailingCommitConn:
        def __getattr__(self, name):
            return getattr(real_conn, name)

        def commit(self):
            raise sqlite3.OperationalError("disk I/O error")

    db.conn = _FailingCommitConn()

    with pytest.raises(sqlite3.OperationalError):
        db.log_trade(1, "kalshi", "yes", 0.5, 10.0, "pending")

    assert fired and fired[0][0] == "log_trade"
    assert "disk I/O" in fired[0][1]


def test_off_allowlist_alert_is_critical():
    """The off-allowlist guardrail-breach attempt pages immediately (CRITICAL).
    Pairs with the executor veto, which fires this when an opportunity routes to a
    venue outside ENABLED_EXECUTION_PLATFORMS (hard guardrail: off-allowlist orders = 0)."""
    mgr = _mgr()
    assert mgr.alert_off_allowlist("polymarket", opp_type="Cross", market="ETH-USD") is True
    rec = _last(mgr)
    assert AlertType.OFF_ALLOWLIST.value in str(rec["type"])
    assert Severity.CRITICAL.value in str(rec["severity"])
    assert "polymarket" in rec["message"]
    assert "OFF-ALLOWLIST" in rec["message"]
