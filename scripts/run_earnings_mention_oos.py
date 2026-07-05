"""Earnings-mention NO-harvest OOS logger — the live weekly runner.

Weekly entry point for ``earnings_mention.py``: snapshots newly in-window
company-KPI / earnings-mention Kalshi markets, resolves previously-pending
snapshots against their realized settlement via ``fetch_market``, accumulates
the resolved history in a local JSON state file across runs, recomputes the
OOS richness stats + pre-registered verdict, and Telegram-tickets the
running verdict. Detection/logging only — this script never places an
order, never touches capital, and has no LLM in its path. See
docs/plans/08-earnings-mention-oos.md.

State: v1 persists locally as JSON, restored across scheduled runs via
``actions/cache`` — the same convention scripts/run_edgar_scan.py uses for
its dedup state file. This intentionally does NOT write to the shared
Supabase ``pnl`` table: that table is realized P&L (see
scripts/run_pnl_digest.py), and this logger produces $0 statistical rows
that would corrupt its rollups. Spec step 6 gates "add Supabase secrets ->
activate weekly cron" as an explicit operator follow-up once a dedicated
table exists — not assumed here.

Cron-safe at every missing-config boundary: no Kalshi creds, a failed
auth, or missing Telegram creds all log a warning and exit 0 rather than
raising.

Usage:
    python scripts/run_earnings_mention_oos.py [--state-file PATH] [--always-alert]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow `from earnings_mention import ...` / `from kalshi_api import ...` when
# run as a script from the repo root (scripts/ would otherwise be sys.path[0]).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from earnings_mention import (  # noqa: E402
    OosStats,
    Resolved,
    Snapshot,
    compute_oos_stats,
    resolve_settlements,
    snapshot_open_markets,
    verdict,
)
from kalshi_api import KalshiClient  # noqa: E402
from notifier import WebhookNotifier  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# A pending snapshot that is still unresolved this long after it was taken is
# almost certainly a voided/cancelled market (no yes/no result ever lands) —
# drop it so one bad ticker can't grow `pending` forever.
STALE_PENDING_DAYS = 30.0

_EMPTY_STATE = {"pending": [], "resolved": [], "last_verdict": "continue"}


# --------------------------------------------------------------------------- #
# JSON (de)serialization for the two earnings_mention dataclasses
# --------------------------------------------------------------------------- #
def _snapshot_to_dict(s: Snapshot) -> dict:
    return {
        "ticker": s.ticker,
        "snapshot_ts": s.snapshot_ts,
        "hours_to_close": s.hours_to_close,
        "yes_price": s.yes_price,
        "no_price": s.no_price,
        "volume": s.volume,
        "series": s.series,
    }


def _snapshot_from_dict(d: dict) -> Snapshot:
    return Snapshot(
        ticker=d["ticker"],
        snapshot_ts=d["snapshot_ts"],
        hours_to_close=d["hours_to_close"],
        yes_price=d["yes_price"],
        no_price=d["no_price"],
        volume=d["volume"],
        series=d["series"],
    )


def _resolved_to_dict(r: Resolved, resolved_ts: str) -> dict:
    return {
        "ticker": r.ticker,
        "yes_price": r.yes_price,
        "outcome": r.outcome,
        "series": r.series,
        "resolved_ts": resolved_ts,
    }


def _resolved_from_dict(d: dict) -> Resolved:
    return Resolved(ticker=d["ticker"], yes_price=d["yes_price"], outcome=d["outcome"], series=d["series"])


# --------------------------------------------------------------------------- #
# State (accumulate OOS resolutions across weekly runs)
# --------------------------------------------------------------------------- #
def load_state(path: Path | None) -> dict:
    """Load {pending, resolved, last_verdict} from the JSON state file.

    A missing file or corrupt JSON degrades to an empty state rather than
    crashing the cron — the OOS sample just starts (or restarts) from zero.
    """
    if not path or not path.exists():
        return dict(_EMPTY_STATE)
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError) as exc:
        logger.warning("OOS state read failed (%s) — treating as empty", exc)
        return dict(_EMPTY_STATE)
    if not isinstance(data, dict):
        return dict(_EMPTY_STATE)
    return {
        "pending": data.get("pending", []),
        "resolved": data.get("resolved", []),
        "last_verdict": data.get("last_verdict", "continue"),
    }


def save_state(path: Path | None, state: dict) -> None:
    """Persist {pending, resolved, last_verdict} to the JSON state file."""
    if not path:
        return
    try:
        path.write_text(json.dumps(state))
    except OSError as exc:
        logger.warning("OOS state write failed: %s", exc)


def _drop_stale_pending(pending: list[Snapshot], now: datetime, max_age_days: float = STALE_PENDING_DAYS) -> list[Snapshot]:
    """Drop pending snapshots that have sat unresolved past ``max_age_days``.

    A market can close without ever reaching a settled yes/no result (e.g.
    voided/cancelled) — without this guard such a ticker would stay in
    ``pending`` and get re-queried by resolve_settlements on every run
    indefinitely.
    """
    kept = []
    for s in pending:
        try:
            age_days = (now - datetime.fromisoformat(s.snapshot_ts)).total_seconds() / 86400.0
        except (ValueError, TypeError):
            kept.append(s)  # can't parse -> don't guess, keep it
            continue
        if age_days > max_age_days:
            logger.warning(
                "Dropping stale pending snapshot %s (age %.1fd, never settled yes/no)",
                s.ticker, age_days,
            )
            continue
        kept.append(s)
    return kept


# --------------------------------------------------------------------------- #
# One weekly cycle — deterministic given (client, now, state) beyond the
# client's own network calls. Fully testable with a fake client, no network.
# --------------------------------------------------------------------------- #
def run_cycle(client, now: datetime, state: dict) -> dict:
    """Snapshot, resolve, accumulate, and compute the running OOS verdict.

    Returns the updated persistable state ({pending, resolved, last_verdict})
    plus three caller-facing extras the caller pops before saving:
    ``_stats`` (OosStats), ``_prev_verdict`` (str), ``_new_resolved`` (int).
    """
    pending = [_snapshot_from_dict(d) for d in state.get("pending", [])]
    resolved_dicts = list(state.get("resolved", []))
    resolved_objs = [_resolved_from_dict(d) for d in resolved_dicts]

    already_seen = {s.ticker for s in pending} | {r.ticker for r in resolved_objs}
    fresh = snapshot_open_markets(client, now)
    pending = pending + [s for s in fresh if s.ticker not in already_seen]
    pending = _drop_stale_pending(pending, now)

    newly_resolved = resolve_settlements(client, pending)
    resolved_tickers = {r.ticker for r in newly_resolved}
    pending = [s for s in pending if s.ticker not in resolved_tickers]

    resolved_ts = now.isoformat()
    resolved_dicts = resolved_dicts + [_resolved_to_dict(r, resolved_ts) for r in newly_resolved]
    resolved_objs = resolved_objs + list(newly_resolved)

    stats = compute_oos_stats(resolved_objs)
    verdict_str = verdict(stats)

    return {
        "pending": [_snapshot_to_dict(s) for s in pending],
        "resolved": resolved_dicts,
        "last_verdict": verdict_str,
        "_stats": stats,
        "_prev_verdict": state.get("last_verdict", "continue"),
        "_new_resolved": len(newly_resolved),
    }


def _should_alert(new_resolved: int, prev_verdict: str, verdict_str: str, always_alert: bool) -> bool:
    """Alert on real signal (a new resolution or a verdict flip), or if forced.

    Keeps a weekly cron from spamming the same "continue, n=0" message every
    run while still surfacing the one thing that actually matters: the OOS
    verdict changing, e.g. continue -> pursue.
    """
    return new_resolved > 0 or verdict_str != prev_verdict or always_alert


def format_message(stats: OosStats, verdict_str: str, new_resolved: int) -> str:
    """Render the running-verdict Telegram ticket text."""
    lines = [
        "\U0001F4CA Earnings-Mention OOS Logger — weekly run",
        f"New settlements resolved this run: {new_resolved}",
        f"OOS sample (11-50c band): n={stats.n}, mean richness={stats.mean_richness_pts:+.2f}pts, z={stats.z:.2f}",
        f"Verdict: {verdict_str.upper()}",
    ]
    if stats.by_category:
        top = sorted(stats.by_category.items(), key=lambda kv: -kv[1][0])[:5]
        lines.append(
            "By category (n/mean pts): "
            + ", ".join(f"{series}={n}/{mean:+.1f}" for series, (n, mean) in top)
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="Earnings-mention NO-harvest OOS logger (weekly)")
    parser.add_argument("--state-file", type=Path, default=None,
                        help="JSON file accumulating pending/resolved snapshots across runs")
    parser.add_argument("--always-alert", action="store_true",
                        help="Telegram-ticket the running verdict even with no new signal this run")
    args = parser.parse_args()

    api_key_id = os.getenv("KALSHI_API_KEY_ID")
    private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH")
    private_key_b64 = os.getenv("KALSHI_PRIVATE_KEY_BASE64")
    if not api_key_id or not (private_key_path or private_key_b64):
        logger.warning(
            "KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PATH (or _BASE64) not set — "
            "cannot read Kalshi markets. Cron-safe exit."
        )
        return

    client = KalshiClient()
    if private_key_b64:
        authed = client.login_with_api_key(api_key_id, private_key_base64=private_key_b64)
    else:
        authed = client.login_with_api_key(api_key_id, private_key_path=os.path.expanduser(private_key_path))
    if not authed:
        logger.warning("Kalshi auth failed. Cron-safe exit.")
        return

    now = datetime.now(timezone.utc)
    state = load_state(args.state_file)
    result = run_cycle(client, now, state)

    stats: OosStats = result.pop("_stats")
    prev_verdict: str = result.pop("_prev_verdict")
    new_resolved: int = result.pop("_new_resolved")
    verdict_str: str = result["last_verdict"]

    logger.info(
        "OOS cycle: pending=%d resolved_total=%d new_resolved=%d n=%d mean=%.2fpts z=%.2f verdict=%s",
        len(result["pending"]), len(result["resolved"]), new_resolved,
        stats.n, stats.mean_richness_pts, stats.z, verdict_str,
    )
    save_state(args.state_file, result)

    if not _should_alert(new_resolved, prev_verdict, verdict_str, args.always_alert):
        logger.info("No new resolutions and verdict unchanged (%s) — skipping Telegram.", verdict_str)
        return

    message = format_message(stats, verdict_str, new_resolved)
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — printing instead:\n%s", message)
        return
    WebhookNotifier("telegram").notify_text(message)
    logger.info("Telegram ticket sent (verdict=%s, new_resolved=%d).", verdict_str, new_resolved)


if __name__ == "__main__":
    main()
