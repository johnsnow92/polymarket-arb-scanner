"""Deterministic KPI digest formatter (15-KPI-DASHBOARD-SPEC)."""
from __future__ import annotations

from db import TradeDB
from digest import build_digest, send_digest


def test_digest_on_empty_db_is_well_formed():
    db = TradeDB(":memory:")
    text = build_digest(db)
    assert "📊 Daily Digest" in text
    assert "✅ Guardrails clean" in text
    assert "P&L: today $0.00" in text
    assert "Open positions: 0" in text
    assert "Ops alerts (24h):" in text
    assert "off-allowlist=0" in text


def test_off_allowlist_breach_is_pinned():
    db = TradeDB(":memory:")
    db.log_alert("OFF_ALLOWLIST", "CRITICAL", "vetoed cross-venue")
    text = build_digest(db)
    assert "🔴 BREACH: off allowlist attempts = 1" in text
    assert "✅ Guardrails clean" not in text


def test_naked_leg_breach_is_pinned():
    db = TradeDB(":memory:")
    db.log_alert("PARTIAL_FILL", "CRITICAL", "naked exposure")
    text = build_digest(db)
    assert "🔴 BREACH: naked leg events = 1" in text


def test_ops_alert_counts_appear():
    db = TradeDB(":memory:")
    db.log_alert("RATE_LIMIT", "WARNING", "429")
    db.log_alert("DB_WRITE_FAILURE", "CRITICAL", "disk")
    text = build_digest(db)
    assert "429=1" in text
    assert "db-write-fail=1" in text


def test_recent_critical_alerts_listed():
    db = TradeDB(":memory:")
    db.log_alert("PARTIAL_FILL", "CRITICAL", "naked exposure on ETH")
    text = build_digest(db)
    assert "Recent CRITICAL" in text
    assert "naked exposure on ETH" in text


def test_open_position_is_listed():
    db = TradeDB(":memory:")
    opp_id = db.log_opportunity(
        opp_type="Binary", market="Test Market", prices="",
        total_cost=10.0, net_profit=1.0, net_roi=0.1, depth=0, action="traded",
    )
    db.create_position(
        opportunity_id=opp_id, market_identifier="Test Market",
        platform="kalshi", expected_pnl=1.5,
    )
    text = build_digest(db)
    assert "Open positions: 1" in text
    assert "kalshi: Test Market" in text


def test_send_digest_calls_notifier_and_returns_text():
    db = TradeDB(":memory:")
    sent = []

    class _Notifier:
        def _send_telegram(self, text):
            sent.append(text)

    text = send_digest(db, notifier=_Notifier())
    assert sent == [text]
    assert "📊 Daily Digest" in text


def test_send_digest_without_notifier_does_not_error():
    db = TradeDB(":memory:")
    text = send_digest(db, notifier=None)
    assert "📊 Daily Digest" in text
