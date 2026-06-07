#!/usr/bin/env python3
"""Threshold tuning loop — completes #20 from the v2 framework.

Runs ``backtest.BacktestEngine`` over a rolling N-day window of
``snapshots.db``, calls ``backtest.build_recommendations`` to surface
suggested adjustments to:
- ``MIN_NET_ROI``
- ``FUZZY_MATCH_THRESHOLD``
- ``MIN_PROFIT_THRESHOLD``

Writes two artefacts to the chosen output directory:
- ``backtest_recommendations.json`` (raw, machine-readable)
- ``tuning_<YYYY-MM-DD>.md`` (human-readable report with per-strategy
  P&L, current vs proposed values, and a one-line apply hint)

Designed to run on demand (``python scripts/tune.py``) or from a
nightly cron / continuous-mode hook (the existing
``BACKTEST_RUN_INTERVAL`` in ``continuous.py`` already invokes
``backtest.write_recommendations``; this script extends that path
with the markdown emit).

Usage:
    python scripts/tune.py                 # 30-day window, DATA_DIR
    python scripts/tune.py --window-days 7
    python scripts/tune.py --output ./out
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

# Ensure parent directory is importable when invoked via ``python scripts/tune.py``.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Markdown formatter (pure function — easy to unit test)
# ---------------------------------------------------------------------------


def format_recommendations_md(rec: dict, *, window_days: int) -> str:
    """Render a build_recommendations() dict as a markdown report."""
    generated = rec.get("generated_at", "")
    total = int(rec.get("total_trades", 0))
    win_rate = float(rec.get("win_rate", 0.0))
    current = rec.get("current", {}) or {}
    recommended = rec.get("recommended", {}) or {}
    by_strategy = rec.get("by_strategy", {}) or {}

    lines: list[str] = []
    lines.append(f"# Tuning Report — {generated}")
    lines.append("")
    lines.append(f"- **Window:** rolling {window_days} days")
    lines.append(f"- **Trades evaluated:** {total}")
    lines.append(f"- **Overall win rate:** {win_rate * 100:.1f}%")
    lines.append("")
    lines.append("## Threshold recommendations")
    lines.append("")
    lines.append("| Variable | Current | Proposed | Δ |")
    lines.append("|---|---:|---:|---|")
    for key in sorted(set(list(current.keys()) + list(recommended.keys()))):
        cur = current.get(key)
        prop = recommended.get(key)
        delta = _format_delta(cur, prop)
        lines.append(
            f"| `{key}` | {_format_num(cur)} | {_format_num(prop)} | {delta} |"
        )
    lines.append("")

    visible_strategies = {
        name: stats for name, stats in by_strategy.items()
        if not name.startswith("__")
    }
    if visible_strategies:
        lines.append("## Per-strategy breakdown")
        lines.append("")
        lines.append("| Strategy | Win rate | Avg profit |")
        lines.append("|---|---:|---:|")
        for strat in sorted(visible_strategies.keys()):
            stats = visible_strategies[strat] or {}
            wr = float(stats.get("win_rate", 0.0))
            avg = float(stats.get("avg_profit", 0.0))
            lines.append(f"| {strat} | {wr * 100:.1f}% | {avg:+.4f} |")
        lines.append("")

    lines.append("## Apply")
    lines.append("")
    lines.append(
        "These are advisory only — review the per-strategy table before "
        "exporting any of the proposed values as env vars. Suggested "
        "approach: stage the change as a one-line CLAUDE.md note, then "
        "set the env var on Railway alongside the existing config."
    )
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(
        "- Suggestions are derived from win-rate heuristics in "
        "`backtest._suggest_*`. They are deliberately conservative — "
        "changes are bounded so a single bad window can't push a "
        "threshold off a cliff."
    )
    lines.append(
        "- Layer-3 (`MM_MIN_SPREAD`) and Layer-4 (`EVENT_DIVERGENCE_THRESHOLD`) "
        "tuning is not yet wired in. Adding them is a follow-up — see the "
        "v2 framework remediation roadmap."
    )

    return "\n".join(lines) + "\n"


def _format_num(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # Keep small thresholds readable.
        if abs(value) < 1:
            return f"{value:.4f}"
        return f"{value:.2f}"
    return str(value)


def _format_delta(cur: Any, prop: Any) -> str:
    try:
        if cur is None or prop is None:
            return "—"
        cur_f = float(cur)
        prop_f = float(prop)
    except (TypeError, ValueError):
        return "—"
    diff = prop_f - cur_f
    if diff == 0:
        return "no change"
    pct = (diff / cur_f * 100) if cur_f else 0.0
    arrow = "↑" if diff > 0 else "↓"
    return f"{arrow} {abs(diff):.4f} ({pct:+.1f}%)"


# ---------------------------------------------------------------------------
# Tune runner
# ---------------------------------------------------------------------------


def run_tune(
    *,
    window_days: int,
    output_dir: str,
    now: datetime | None = None,
    backtest_engine=None,
    write_json: bool = True,
    json_path: str | None = None,
    write_markdown: bool = True,
) -> dict:
    """Run a tune cycle and write the markdown + JSON artefacts.

    Args:
        window_days: Rolling lookback window.
        output_dir: Directory to write the markdown and JSON files into.
            Created if missing.
        now: Optional fixed timestamp (used by tests).
        backtest_engine: Optional pre-built engine (used by tests).
        write_json: When False, skip the JSON file entirely.
        json_path: Explicit absolute path for the JSON file (file-mode
            CLI invocation). When None, defaults to
            ``output_dir/backtest_recommendations.json``.
        write_markdown: When False, skip the markdown sibling — used in
            file-mode CLI invocation where only the JSON is requested.

    Returns:
        Dict with optional ``markdown_path`` / ``json_path`` and
        ``recommendations`` (the dict from ``build_recommendations``).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    start = now - timedelta(days=window_days)

    if backtest_engine is None:
        from backtest import BacktestEngine
        backtest_engine = BacktestEngine()

    result = backtest_engine.run(
        start_time=start.isoformat(),
        end_time=now.isoformat(),
    )

    from backtest import build_recommendations
    rec = build_recommendations(result)
    # Override the period_days field so the report matches the actual window.
    rec["period_days"] = window_days

    os.makedirs(output_dir, exist_ok=True)

    out: dict = {"recommendations": rec}

    if write_markdown:
        md_name = f"tuning_{now.strftime('%Y-%m-%d')}.md"
        md_path = os.path.join(output_dir, md_name)
        md_text = format_recommendations_md(rec, window_days=window_days)
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(md_text)
        logger.info("Tuning report written to %s", md_path)
        out["markdown_path"] = md_path

    if write_json:
        target = json_path or os.path.join(output_dir, "backtest_recommendations.json")
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=2)
        out["json_path"] = target
        logger.info("Recommendations JSON written to %s", target)

    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Threshold tuning loop — runs a rolling backtest and writes "
            "a markdown recommendations report (#20)."
        ),
    )
    parser.add_argument(
        "--window-days", "--days", type=int, default=30, dest="window_days",
        help="Rolling backtest window in days (default 30).",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help=(
            "Output target. A path ending in '.json' is treated as a file "
            "and only the JSON recommendations are written. Anything else "
            "is treated as a directory and both the JSON and markdown "
            "report are written into it. Defaults to DATA_DIR/scripts."
        ),
    )
    parser.add_argument(
        "--no-json", action="store_true",
        help="Skip the JSON sibling (markdown-only).",
    )
    parser.add_argument(
        "--log-level", type=str, default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    raw_output = args.output
    file_mode = raw_output is not None and raw_output.lower().endswith(".json")

    if file_mode:
        json_path = os.path.abspath(raw_output)
        output_dir = os.path.dirname(json_path) or "."
    else:
        output_dir = raw_output or os.getenv("DATA_DIR") or os.path.dirname(
            os.path.abspath(__file__)
        )
        json_path = None

    paths = run_tune(
        window_days=args.window_days,
        output_dir=output_dir,
        write_json=not args.no_json,
        json_path=json_path,
        write_markdown=not file_mode,
    )

    if file_mode:
        target = paths.get("json_path") or json_path
        print(f"Backtest recommendations written to {target}")
    else:
        md_path = paths["markdown_path"]
        print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
