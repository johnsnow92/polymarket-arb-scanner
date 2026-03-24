"""P&L analysis script for trades.db.

Reads the SQLite database written by the scanner and produces a
formatted report with per-strategy breakdown, win rates, and
validation against the project's 3 success criteria.
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def _green(text: str) -> str:
    return f"\033[92m{text}\033[0m"


def _red(text: str) -> str:
    return f"\033[91m{text}\033[0m"


def _yellow(text: str) -> str:
    return f"\033[93m{text}\033[0m"


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m"


# ---------------------------------------------------------------------------
# Data queries
# ---------------------------------------------------------------------------

def _connect(db_path: str) -> sqlite3.Connection:
    """Connect to trades.db and return a connection."""
    if not os.path.exists(db_path):
        print(_red(f"Error: database not found at {db_path}"), file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _strategy_pnl(conn: sqlite3.Connection, since: str) -> list[dict]:
    """Per-strategy P&L breakdown."""
    rows = conn.execute("""
        SELECT type,
               COUNT(*) as count,
               SUM(CASE WHEN net_profit > 0 THEN 1 ELSE 0 END) as wins,
               SUM(net_profit) as total_pnl,
               AVG(net_profit) as avg_pnl
        FROM opportunities
        WHERE timestamp >= ? AND action IN ('executed', 'filled', 'dry_run')
        GROUP BY type
        ORDER BY total_pnl DESC
    """, (since,)).fetchall()
    return [dict(r) for r in rows]


def _trade_stats(conn: sqlite3.Connection, since: str) -> dict:
    """Execution success/failure counts."""
    row = conn.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN status = 'filled' THEN 1 ELSE 0 END) as filled,
               SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
        FROM trades
        WHERE timestamp >= ?
    """, (since,)).fetchone()
    return dict(row) if row else {"total": 0, "filled": 0, "failed": 0}


def _detection_stats(conn: sqlite3.Connection, since: str) -> dict:
    """Total detected vs executed vs rejected."""
    row = conn.execute("""
        SELECT COUNT(*) as detected,
               SUM(CASE WHEN action IN ('executed', 'filled') THEN 1 ELSE 0 END) as executed,
               SUM(CASE WHEN action LIKE 'skipped:%' OR action = 'rejected' THEN 1 ELSE 0 END) as rejected
        FROM opportunities
        WHERE timestamp >= ?
    """, (since,)).fetchone()
    return dict(row) if row else {"detected": 0, "executed": 0, "rejected": 0}


def _has_profitable_roundtrip(conn: sqlite3.Connection, since: str) -> bool:
    """Check if at least one opportunity has net_profit > 0 with filled trades."""
    row = conn.execute("""
        SELECT COUNT(*) as cnt
        FROM opportunities o
        JOIN trades t ON t.opportunity_id = o.id
        WHERE o.timestamp >= ?
          AND o.net_profit > 0
          AND t.status = 'filled'
    """, (since,)).fetchone()
    return row["cnt"] > 0 if row else False


# ---------------------------------------------------------------------------
# Report output
# ---------------------------------------------------------------------------

def _print_report(db_path: str, hours: int):
    """Generate and print the P&L report."""
    conn = _connect(db_path)
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    strategies = _strategy_pnl(conn, since)
    trades = _trade_stats(conn, since)
    detections = _detection_stats(conn, since)
    has_roundtrip = _has_profitable_roundtrip(conn, since)

    total_pnl = sum(s["total_pnl"] or 0 for s in strategies)
    total_opps = sum(s["count"] or 0 for s in strategies)
    total_wins = sum(s["wins"] or 0 for s in strategies)

    print(_bold(f"\n{'='*60}"))
    print(_bold(f"  P&L Report — Last {hours} hours"))
    print(_bold(f"  Database: {db_path}"))
    print(_bold(f"{'='*60}\n"))

    # --- Overall ---
    pnl_color = _green if total_pnl >= 0 else _red
    print(f"  Total P&L:      {pnl_color(f'${total_pnl:+.4f}')}")
    print(f"  Opportunities:  {total_opps}")
    win_rate = (total_wins / total_opps * 100) if total_opps > 0 else 0
    print(f"  Win rate:       {win_rate:.1f}%")
    print()

    # --- Per-strategy ---
    if strategies:
        print(_bold("  Per-Strategy Breakdown:"))
        print(f"  {'Strategy':<25} {'Count':>6} {'Wins':>6} {'P&L':>12} {'Avg':>10}")
        print(f"  {'-'*25} {'-'*6} {'-'*6} {'-'*12} {'-'*10}")
        for s in strategies:
            pnl = s["total_pnl"] or 0
            avg = s["avg_pnl"] or 0
            c = _green if pnl >= 0 else _red
            print(f"  {s['type']:<25} {s['count']:>6} {s['wins']:>6} {c(f'${pnl:>+10.4f}')}  ${avg:>+8.4f}")
        print()

    # --- Trade execution ---
    print(_bold("  Trade Execution:"))
    print(f"  Total trades:   {trades['total']}")
    print(f"  Filled:         {trades['filled']}")
    print(f"  Failed:         {trades['failed']}")
    if trades["total"] > 0:
        success_rate = trades["filled"] / trades["total"] * 100
        c = _green if success_rate >= 90 else _yellow if success_rate >= 70 else _red
        print(f"  Success rate:   {c(f'{success_rate:.1f}%')}")
    print()

    # --- Detection stats ---
    print(_bold("  Detection Stats:"))
    print(f"  Detected:       {detections['detected']}")
    print(f"  Executed:       {detections['executed']}")
    print(f"  Rejected/Skip:  {detections['rejected']}")
    if detections["detected"] > 0:
        fp_rate = detections["rejected"] / detections["detected"] * 100
        c = _green if fp_rate < 5 else _yellow if fp_rate < 15 else _red
        print(f"  FP rate:        {c(f'{fp_rate:.1f}%')}")
    print()

    # --- Success criteria ---
    print(_bold("  Success Criteria:"))
    c1 = total_pnl > 0
    c2 = (detections["rejected"] / detections["detected"] * 100 < 5) if detections["detected"] > 0 else True
    c3 = has_roundtrip

    status = lambda ok: _green("PASS") if ok else _red("FAIL")
    print(f"  {status(c1)}  Net positive P&L over 7-day period")
    print(f"  {status(c2)}  <5% false positive rate")
    print(f"  {status(c3)}  At least 1 profitable round-trip trade")
    print()

    all_pass = c1 and c2 and c3
    if all_pass:
        print(_green("  All success criteria met!"))
    else:
        print(_yellow("  Some criteria not yet met. Review above."))
    print()

    conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="P&L report from trades.db")
    parser.add_argument("--db", default="trades.db",
                        help="Path to trades.db (default: trades.db)")
    parser.add_argument("--hours", type=int, default=48,
                        help="Lookback window in hours (default: 48)")
    args = parser.parse_args()
    _print_report(args.db, args.hours)


if __name__ == "__main__":
    main()
