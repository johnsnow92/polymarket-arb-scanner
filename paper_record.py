"""Paper-trading window tracker: daily digest + one-time completion alert.

The go-live gate requires a positive paper record over a defined window, but
until now the window existed only as a convention — nothing tracked it, ended
it, or reported on it. This module makes it explicit: once per UTC day it
summarises what the dry-run pipeline detected (by type and by action) to the
configured webhook, and when the window closes it sends a single completion
alert so the operator knows the decision point has arrived.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DAY_SECONDS = 86400.0


class PaperRecordTracker:
    """Tracks the paper-trading window over the opportunities table.

    Args:
        db: TradeDB instance (source of truth for detections).
        notifier: Notifier with ``notify_text``; None disables sends.
        window_start: Epoch seconds of window start; 0/falsy disables the
            tracker entirely (no window configured).
        window_days: Window length in days.
    """

    def __init__(self, db, notifier, window_start: float, window_days: int = 7):
        self.db = db
        self.notifier = notifier
        self.window_start = float(window_start or 0.0)
        self.window_days = window_days
        self._last_digest_day: int | None = None
        self._completion_sent = False

    @property
    def enabled(self) -> bool:
        return bool(self.window_start)

    def on_day_boundary(self, now: float) -> None:
        """Call at (or after) each UTC day boundary; idempotent within a day."""
        if not self.enabled:
            return
        day = int((now - self.window_start) // _DAY_SECONDS)
        if day < 1 or day == self._last_digest_day:
            return
        self._last_digest_day = day

        try:
            summary = self._summarise()
            self._send(self._format_digest(day, summary))
            if day >= self.window_days and not self._completion_sent:
                self._completion_sent = True
                self._send(self._format_completion(summary))
        except Exception as exc:
            logger.warning("Paper-record digest failed: %s", exc)

    # ------------------------------------------------------------------

    def _summarise(self) -> dict:
        since_iso = datetime.fromtimestamp(self.window_start, tz=timezone.utc).isoformat()
        with self.db._lock:
            by_type = self.db.conn.execute(
                """SELECT type, COUNT(*), AVG(net_roi), MAX(net_roi)
                   FROM opportunities WHERE timestamp >= ? GROUP BY type
                   ORDER BY COUNT(*) DESC""",
                (since_iso,),
            ).fetchall()
            by_action = self.db.conn.execute(
                """SELECT action, COUNT(*) FROM opportunities
                   WHERE timestamp >= ? GROUP BY action ORDER BY COUNT(*) DESC""",
                (since_iso,),
            ).fetchall()
        return {"by_type": by_type, "by_action": by_action}

    def _format_digest(self, day: int, summary: dict) -> str:
        total = sum(row[1] for row in summary["by_type"])
        lines = [
            f"📊 arbgrid paper record — day {day}/{self.window_days}",
            f"Detections since window start: {total}",
        ]
        for opp_type, count, avg_roi, max_roi in summary["by_type"]:
            lines.append(
                f"  • {opp_type}: {count} (avg ROI {avg_roi * 100:.2f}%, max {max_roi * 100:.2f}%)")
        if summary["by_action"]:
            actions = ", ".join(f"{action or 'unknown'}={count}" for action, count in summary["by_action"])
            lines.append(f"Outcomes: {actions}")
        if total == 0:
            lines.append("Zero detections above threshold — honest zero, still signal.")
        return "\n".join(lines)

    def _format_completion(self, summary: dict) -> str:
        total = sum(row[1] for row in summary["by_type"])
        return (
            f"🏁 arbgrid paper window COMPLETE ({self.window_days} days). "
            f"{total} detections recorded. Time for the decision review: "
            f"tune-and-walk-the-gate vs Layer-3 pivot. Run /goal for the full read."
        )

    def _send(self, message: str) -> None:
        if not self.notifier:
            return
        try:
            self.notifier.notify_text(message)
        except Exception as exc:
            logger.warning("Paper-record notification failed: %s", exc)
