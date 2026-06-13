"""Structured alerting for execution failures, loss limits, and system health.

Integrates with the existing notifier.py webhook system and provides
rate-limited alerts with severity levels.
"""

import json
import logging
import threading
import time
from collections import deque
from enum import Enum

logger = logging.getLogger(__name__)


class AlertType(str, Enum):
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    LOSS_STREAK = "LOSS_STREAK"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    POSITION_LIMIT = "POSITION_LIMIT"
    WS_DISCONNECT = "WS_DISCONNECT"
    SCAN_FAILURE = "SCAN_FAILURE"
    BALANCE_LOW = "BALANCE_LOW"
    LOSS_SPIKE = "LOSS_SPIKE"
    ZERO_OPP_PERIOD = "ZERO_OPP_PERIOD"
    ZERO_OPP = "ZERO_OPP"
    CREDENTIAL_FAILURE = "CREDENTIAL_FAILURE"
    # Ops/observability alerts (Week-1 safety rails → ClaudeClaw)
    RATE_LIMIT = "RATE_LIMIT"
    PARTIAL_FILL = "PARTIAL_FILL"
    DB_WRITE_FAILURE = "DB_WRITE_FAILURE"
    HEARTBEAT = "HEARTBEAT"
    OFF_ALLOWLIST = "OFF_ALLOWLIST"


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertManager:
    """Rate-limited alerting system that fires alerts via notifier.py.

    Features:
    - Rate limiting: max 1 alert per type per configurable window (default 5 min)
    - Severity levels: INFO, WARNING, CRITICAL
    - Loss streak detection: fires after N consecutive losing trades
    - Loss limit warning: fires at 80% and 100% of daily loss limit
    - Formats alerts for the existing notifier.py webhook
    """

    def __init__(
        self,
        notifier=None,
        rate_limit_seconds: float = 300,
        loss_streak_threshold: int = 5,
        balance_low_threshold: float = 10.0,
        db=None,
    ):
        """
        Args:
            notifier: WebhookNotifier instance (or None for logging-only).
            rate_limit_seconds: Minimum seconds between alerts of the same type.
            loss_streak_threshold: Number of consecutive losses before alert.
            balance_low_threshold: Dollar amount below which BALANCE_LOW fires.
        """
        self.notifier = notifier
        self._db = db
        self.rate_limit_seconds = rate_limit_seconds
        self.loss_streak_threshold = loss_streak_threshold
        self.balance_low_threshold = balance_low_threshold

        # Rate limiting: alert_type -> last fire timestamp
        self._last_fired: dict[str, float] = {}
        self._lock = threading.Lock()

        # Loss streak tracking
        self._trade_results: deque[bool] = deque(maxlen=100)

        # MON-03: Per-strategy loss streak tracking
        # strategy_type -> deque of trade results (True=win, False=loss)
        self._strategy_losses: dict[str, deque] = {}
        # strategy_type -> timestamp of last opportunity found
        self._strategy_last_opp_time: dict[str, float] = {}

        # Recent alerts ring buffer
        self._recent_alerts: deque[dict] = deque(maxlen=200)

        # Track whether 80% and 100% warnings have fired today
        self._loss_80_fired = False
        self._loss_100_fired = False

        # MONITOR-03: Anomaly detection state
        # Rolling window of absolute loss amounts (maxlen=20) for spike detection
        self._trade_losses: deque[float] = deque(maxlen=20)
        # Count of consecutive scans that returned zero opportunities
        self._zero_opp_count: int = 0

    def set_db(self, db) -> None:
        """Attach a TradeDB so alerts are durably persisted for the ops_alerts view
        and the KPI digest. Safe to call once at startup."""
        self._db = db

    def alert(
        self,
        alert_type: str | AlertType,
        severity: str | Severity,
        message: str,
        details: dict | None = None,
    ) -> bool:
        """Fire an alert if not rate-limited.

        Args:
            alert_type: One of the AlertType values.
            severity: One of the Severity values.
            message: Human-readable alert message.
            details: Optional extra context.

        Returns:
            True if the alert was actually sent (not rate-limited), False otherwise.
        """
        alert_type_str = str(alert_type.value if isinstance(alert_type, AlertType) else alert_type)
        severity_str = str(severity.value if isinstance(severity, Severity) else severity)

        # Rate limiting check
        with self._lock:
            now = time.time()
            last = self._last_fired.get(alert_type_str, 0)
            if now - last < self.rate_limit_seconds:
                logger.debug("Alert %s rate-limited (last fired %.0fs ago)",
                             alert_type_str, now - last)
                return False
            self._last_fired[alert_type_str] = now

        # Build alert record
        alert_record = {
            "type": alert_type_str,
            "severity": severity_str,
            "message": message,
            "details": details or {},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "epoch": time.time(),
        }

        self._recent_alerts.append(alert_record)

        # Durable persistence for the ops_alerts view + KPI digest (best-effort).
        if self._db is not None:
            try:
                self._db.log_alert(
                    alert_type=alert_type_str,
                    severity=severity_str,
                    message=message,
                    details=json.dumps(details or {}),
                    timestamp=alert_record["timestamp"],
                    epoch=alert_record["epoch"],
                )
            except Exception:
                logger.debug("alert persistence failed", exc_info=True)

        # Log it
        log_level = {
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "CRITICAL": logging.CRITICAL,
        }.get(severity_str, logging.WARNING)
        logger.log(log_level, "ALERT [%s/%s]: %s", alert_type_str, severity_str, message)

        # Send via notifier if available
        if self.notifier:
            self._send_webhook(alert_record)

        return True

    # ------------------------------------------------------------------
    # Ops/observability helpers (429, partial-fill, DB-write-failure, heartbeat)
    # ------------------------------------------------------------------

    def alert_rate_limit(
        self, venue: str, endpoint: str | None = None, retry_after: float | None = None
    ) -> bool:
        """Fire a 429 rate-limit alert for a venue/endpoint."""
        msg = f"HTTP 429 rate-limited on {venue}"
        if endpoint:
            msg += f" {endpoint}"
        if retry_after is not None:
            msg += f" (retry after {retry_after:.0f}s)"
        return self.alert(
            AlertType.RATE_LIMIT, Severity.WARNING, msg,
            {"venue": venue, "endpoint": endpoint, "retry_after": retry_after},
        )

    def alert_partial_fill(
        self, opp_type: str, filled_legs: int, total_legs: int, market: str | None = None
    ) -> bool:
        """Fire a CRITICAL partial-fill alert: one or more legs filled while others did
        not, leaving naked exposure on the filled leg(s). Surfaces every naked-leg event
        for the hard guardrail (target: naked-leg events = 0)."""
        msg = (
            f"PARTIAL FILL on {opp_type}: {filled_legs}/{total_legs} legs filled "
            f"— naked exposure"
        )
        if market:
            msg += f" on {market}"
        return self.alert(
            AlertType.PARTIAL_FILL, Severity.CRITICAL, msg,
            {"opp_type": opp_type, "filled_legs": filled_legs,
             "total_legs": total_legs, "market": market},
        )

    def alert_db_write_failure(self, operation: str, error: object) -> bool:
        """Fire a CRITICAL DB-write-failure alert (a trade/position record may be lost)."""
        return self.alert(
            AlertType.DB_WRITE_FAILURE, Severity.CRITICAL,
            f"DB write failed during {operation}: {error}",
            {"operation": operation, "error": str(error)},
        )

    def heartbeat(self, component: str = "continuous", extra: dict | None = None) -> bool:
        """Emit a process-liveness heartbeat (INFO). Rate-limited like any alert, so a
        dead process is detectable by the absence of recent heartbeats downstream."""
        return self.alert(
            AlertType.HEARTBEAT, Severity.INFO,
            f"heartbeat: {component} alive",
            {"component": component, **(extra or {})},
        )

    def alert_off_allowlist(
        self, venue: str, opp_type: str | None = None, market: str | None = None
    ) -> bool:
        """Fire a CRITICAL off-allowlist-attempt alert: an opportunity tried to route to a
        venue outside the execution allowlist (it was vetoed — zero orders placed). Hard
        guardrail (target: off-allowlist orders = 0) → immediate page."""
        msg = f"OFF-ALLOWLIST attempt vetoed: venue(s) {venue}"
        if opp_type:
            msg += f" [{opp_type}]"
        if market:
            msg += f" on {market}"
        return self.alert(
            AlertType.OFF_ALLOWLIST, Severity.CRITICAL, msg,
            {"venue": venue, "opp_type": opp_type, "market": market},
        )

    def check_loss_streak(self, trade_won: bool) -> bool:
        """Record a trade result and fire an alert if a loss streak is detected.

        Args:
            trade_won: True if the trade was profitable, False if it lost.

        Returns:
            True if a LOSS_STREAK alert was fired.
        """
        self._trade_results.append(trade_won)

        # Count consecutive losses from the end
        streak = 0
        for result in reversed(self._trade_results):
            if not result:
                streak += 1
            else:
                break

        if streak >= self.loss_streak_threshold:
            return self.alert(
                AlertType.LOSS_STREAK,
                Severity.CRITICAL,
                f"Loss streak detected: {streak} consecutive losing trades",
                {"streak_length": streak, "threshold": self.loss_streak_threshold},
            )
        return False

    def check_daily_loss(self, current_pnl: float, daily_limit: float) -> bool:
        """Check daily P&L against loss limit and fire warnings at 80% and 100%.

        Args:
            current_pnl: Current daily P&L (negative means loss).
            daily_limit: Maximum allowed daily loss (positive number).

        Returns:
            True if an alert was fired.
        """
        if daily_limit <= 0:
            return False

        loss_ratio = abs(current_pnl) / daily_limit if current_pnl < 0 else 0

        if loss_ratio >= 1.0 and not self._loss_100_fired:
            self._loss_100_fired = True
            self._loss_80_fired = True  # implicitly surpassed
            return self.alert(
                AlertType.DAILY_LOSS_LIMIT,
                Severity.CRITICAL,
                f"Daily loss limit HIT: P&L ${current_pnl:.2f} (limit -${daily_limit:.2f})",
                {"current_pnl": current_pnl, "daily_limit": daily_limit, "ratio": loss_ratio},
            )
        elif loss_ratio >= 0.8 and not self._loss_80_fired:
            self._loss_80_fired = True
            return self.alert(
                AlertType.DAILY_LOSS_LIMIT,
                Severity.WARNING,
                f"Approaching daily loss limit: P&L ${current_pnl:.2f} ({loss_ratio:.0%} of -${daily_limit:.2f})",
                {"current_pnl": current_pnl, "daily_limit": daily_limit, "ratio": loss_ratio},
            )

        return False

    def check_position_limit(self, current: int, max_limit: int) -> bool:
        """Check position count against limit and warn at 90% and 100%.

        Returns:
            True if an alert was fired.
        """
        if max_limit <= 0:
            return False

        if current >= max_limit:
            return self.alert(
                AlertType.POSITION_LIMIT,
                Severity.CRITICAL,
                f"Max positions reached: {current}/{max_limit}",
                {"current": current, "max_limit": max_limit},
            )
        elif current >= max_limit * 0.9:
            return self.alert(
                AlertType.POSITION_LIMIT,
                Severity.WARNING,
                f"Approaching position limit: {current}/{max_limit}",
                {"current": current, "max_limit": max_limit},
            )

        return False

    def record_trade_result(self, profit: float) -> None:
        """Record a trade result for loss-spike detection.

        Only losing trades (profit < 0) are stored in the rolling window.

        Args:
            profit: Net profit of the trade (negative means a loss).
        """
        if profit < 0:
            self._trade_losses.append(abs(profit))

    def check_loss_spike(self, loss_amount: float) -> bool:
        """Fire a CRITICAL alert if a single loss is more than 3x the rolling average.

        Guards against false positives by requiring at least 10 trades in the
        rolling window before any alert can fire (Pitfall 5 guard).

        Args:
            loss_amount: Absolute value of the loss to evaluate.

        Returns:
            True if a LOSS_SPIKE alert was fired.
        """
        if len(self._trade_losses) < 10:
            return False
        avg = sum(self._trade_losses) / len(self._trade_losses)
        if avg > 0 and loss_amount > 3 * avg:
            return self.alert(
                AlertType.LOSS_SPIKE,
                Severity.CRITICAL,
                f"Loss spike detected: ${loss_amount:.2f} is {loss_amount / avg:.1f}x the rolling average (${avg:.2f})",
                {"loss": loss_amount, "avg": avg, "ratio": loss_amount / avg},
            )
        return False

    def check_zero_opp_period(self, opportunities_found: int) -> bool:
        """Fire a WARNING alert after 5+ consecutive scans with zero opportunities.

        Resets the counter whenever at least one opportunity is found.

        Args:
            opportunities_found: Number of opportunities found in this scan.

        Returns:
            True if a ZERO_OPP_PERIOD alert was fired.
        """
        if opportunities_found > 0:
            self._zero_opp_count = 0
            return False
        self._zero_opp_count += 1
        if self._zero_opp_count >= 5:
            return self.alert(
                AlertType.ZERO_OPP_PERIOD,
                Severity.WARNING,
                f"No opportunities found for {self._zero_opp_count} consecutive scans",
                {"consecutive_empty_scans": self._zero_opp_count},
            )
        return False

    def check_strategy_loss_streak(self, strategy_type: str, trade_won: bool) -> bool:
        """Record a per-strategy trade result and fire alert after 3 consecutive losses.

        Args:
            strategy_type: Strategy name (e.g., "binary", "cross", "kalshi").
            trade_won: True if trade was profitable, False if it lost.

        Returns:
            True if a LOSS_STREAK alert was fired, False otherwise.
        """
        try:
            # Initialize per-strategy deque if first time seeing this strategy
            if strategy_type not in self._strategy_losses:
                self._strategy_losses[strategy_type] = deque(maxlen=100)

            # Append trade result
            self._strategy_losses[strategy_type].append(trade_won)

            # Count consecutive trailing losses (from end backwards)
            losses = 0
            for result in reversed(self._strategy_losses[strategy_type]):
                if not result:  # False = loss
                    losses += 1
                else:
                    break

            # Fire alert only on exact 3 losses (first time hitting threshold)
            if losses == 3:
                return self.alert(
                    AlertType.LOSS_STREAK,
                    Severity.WARNING,
                    f"Strategy {strategy_type}: 3 consecutive losses",
                    {
                        "strategy": strategy_type,
                        "loss_count": losses,
                        "lookback_trades": len(self._strategy_losses[strategy_type]),
                    },
                )
        except Exception as e:
            logger.warning("Error in check_strategy_loss_streak: %s", str(e))

        return False

    def check_zero_opp_period_per_strategy(self, strategy_opportunities: dict[str, int]) -> None:
        """Check per-strategy zero-opportunity periods (30-minute windows).

        Args:
            strategy_opportunities: Dict mapping strategy_type -> count of opportunities found in this scan.
        """
        try:
            now = time.time()
            for strategy_type, count in strategy_opportunities.items():
                if count > 0:
                    # Update last opportunity time for this strategy
                    self._strategy_last_opp_time[strategy_type] = now
                else:
                    # Check if this strategy has been idle for 30+ minutes (1800 seconds)
                    last_opp = self._strategy_last_opp_time.get(strategy_type, now)
                    if now - last_opp >= 1800:
                        # Fire alert (rate limiting handled by AlertManager.alert)
                        self.alert(
                            AlertType.ZERO_OPP,
                            Severity.INFO,
                            f"Strategy {strategy_type}: no opportunities for 30+ minutes",
                            {"strategy": strategy_type, "idle_seconds": int(now - last_opp)},
                        )
        except Exception as e:
            logger.warning("Error in check_zero_opp_period_per_strategy: %s", str(e))

    def record_strategy_opportunity(self, strategy_type: str) -> None:
        """Record that an opportunity was found for a strategy.

        Helper called from continuous.py to ensure tracking is initialized.

        Args:
            strategy_type: Strategy name to record.
        """
        try:
            now = time.time()
            if strategy_type not in self._strategy_last_opp_time:
                self._strategy_last_opp_time[strategy_type] = now
        except Exception as e:
            logger.warning("Error in record_strategy_opportunity: %s", str(e))

    def get_recent_alerts(self, count: int = 20) -> list[dict]:
        """Return the most recent alerts.

        Args:
            count: Maximum number of alerts to return.

        Returns:
            List of alert dicts, newest first.
        """
        alerts = list(self._recent_alerts)
        alerts.reverse()
        return alerts[:count]

    def reset_daily(self):
        """Reset daily state (call at midnight)."""
        self._loss_80_fired = False
        self._loss_100_fired = False
        self._trade_results.clear()

    def _send_webhook(self, alert_record: dict):
        """Format and send an alert via the notifier webhook."""
        if not self.notifier or not hasattr(self.notifier, 'url') or not self.notifier.url:
            return

        severity = alert_record["severity"]
        alert_type = alert_record["type"]
        message = alert_record["message"]

        # Build a synthetic opportunity-like dict so we can reuse notifier._send_raw
        severity_emoji = {"INFO": "info", "WARNING": "warning", "CRITICAL": "rotating_light"}
        emoji = severity_emoji.get(severity, "bell")

        url = self.notifier.url
        if "hooks.slack.com" in url:
            payload = {"text": f":{emoji}: *[{severity}]* {alert_type}\n{message}"}
        elif "discord.com/api/webhooks" in url:
            payload = {"content": f"**[{severity}]** {alert_type}\n{message}"}
        else:
            payload = {
                "event": "alert",
                "type": alert_type,
                "severity": severity,
                "message": message,
                "details": alert_record.get("details", {}),
                "timestamp": alert_record.get("timestamp", ""),
            }

        # Fire-and-forget via notifier's raw send
        if hasattr(self.notifier, '_send_raw'):
            import threading as _threading
            thread = _threading.Thread(
                target=self.notifier._send_raw, args=(payload,), daemon=True)
            thread.start()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

alert_manager = AlertManager()
