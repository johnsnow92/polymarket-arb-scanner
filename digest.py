"""Deterministic daily KPI digest (no LLM in the path) — 15-KPI-DASHBOARD-SPEC.

Assembles the digest sections sourceable from the local TradeDB — P&L, open
positions, the two hard-guardrail KPIs, and ops alerts — into a Telegram-ready
string. P&L-by-lane and cross-venue cash/margin lines require Supabase + live
fills and are added when those land. The formatter is pure and deterministic;
sending is a thin, optional wrapper so the core stays unit-testable.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Hard guardrails (15-KPI-DASHBOARD-SPEC): both must stay at 0 or it's a breach.
_HARD_GUARDRAILS = ("off_allowlist_attempts", "naked_leg_events")


def build_digest(db, window_hours: float = 24.0, date: str | None = None) -> str:
    """Build the deterministic daily digest string from the local TradeDB."""
    lines: list[str] = []
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    win = int(window_hours)

    kpis = db.guardrail_kpi_counts(window_hours=window_hours)
    breaches = [g for g in _HARD_GUARDRAILS if kpis.get(g, 0) > 0]

    lines.append(f"📊 Daily Digest — {day}")

    # Pin any hard-guardrail breach to the top (15-KPI-DASHBOARD-SPEC #9).
    if breaches:
        for g in breaches:
            lines.append(f"🔴 BREACH: {g.replace('_', ' ')} = {kpis[g]} (target 0)")
    else:
        lines.append("✅ Guardrails clean: off-allowlist 0, naked-leg 0")

    # #1 P&L (local realized + cumulative; by-lane awaits the Supabase ledger).
    try:
        lines.append(
            f"P&L: today ${db.get_daily_pnl():,.2f} · cumulative ${db.get_cumulative_pnl():,.2f}"
        )
    except Exception:
        lines.append("P&L: unavailable")

    # #2 Open positions (per venue).
    try:
        positions = db.get_open_positions()
        lines.append(f"Open positions: {len(positions)}")
        for pos in positions[:5]:
            plat = pos.get("platform", "?")
            mkt = pos.get("market_identifier", "?")
            exp = pos.get("expected_pnl") or 0.0
            lines.append(f"  • {plat}: {mkt} (exp ${exp:,.2f})")
    except Exception:
        lines.append("Open positions: unavailable")

    # #8 Ops alerts (window).
    lines.append(
        f"Ops alerts ({win}h): "
        f"429={kpis.get('rate_limits', 0)} · "
        f"partial-fill={kpis.get('naked_leg_events', 0)} · "
        f"db-write-fail={kpis.get('db_write_failures', 0)} · "
        f"off-allowlist={kpis.get('off_allowlist_attempts', 0)}"
    )

    # Recent CRITICAL alerts in the window.
    try:
        since = datetime.now(timezone.utc).timestamp() - window_hours * 3600
        critical = [a for a in db.get_alerts_since(since) if a.get("severity") == "CRITICAL"]
        if critical:
            lines.append(f"Recent CRITICAL ({len(critical)}):")
            for a in critical[:5]:
                lines.append(f"  • [{a.get('alert_type')}] {a.get('message')}")
    except Exception:
        pass

    return "\n".join(lines)


def send_digest(db, notifier=None, window_hours: float = 24.0) -> str:
    """Build the digest and, if a telegram-capable notifier is given, send it.
    Returns the digest text regardless so a cron can also print/log it."""
    text = build_digest(db, window_hours=window_hours)
    if notifier is not None and hasattr(notifier, "_send_telegram"):
        try:
            notifier._send_telegram(text)
        except Exception:
            logger.warning("digest send failed", exc_info=True)
    return text


if __name__ == "__main__":  # pragma: no cover
    from db import TradeDB

    print(build_digest(TradeDB()))
