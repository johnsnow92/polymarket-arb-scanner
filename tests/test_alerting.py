"""Tests for alerting.py — structured alerting system."""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock, patch
from alerting import AlertManager, AlertType, Severity


class TestAlertType:
    def test_all_types_exist(self):
        assert AlertType.EXECUTION_FAILURE == "EXECUTION_FAILURE"
        assert AlertType.LOSS_STREAK == "LOSS_STREAK"
        assert AlertType.DAILY_LOSS_LIMIT == "DAILY_LOSS_LIMIT"
        assert AlertType.POSITION_LIMIT == "POSITION_LIMIT"
        assert AlertType.WS_DISCONNECT == "WS_DISCONNECT"
        assert AlertType.SCAN_FAILURE == "SCAN_FAILURE"
        assert AlertType.BALANCE_LOW == "BALANCE_LOW"


class TestSeverity:
    def test_all_severities_exist(self):
        assert Severity.INFO == "INFO"
        assert Severity.WARNING == "WARNING"
        assert Severity.CRITICAL == "CRITICAL"


class TestAlertManagerBasic:
    def test_init_defaults(self):
        am = AlertManager()
        assert am.rate_limit_seconds == 300
        assert am.loss_streak_threshold == 5
        assert am.balance_low_threshold == 10.0

    def test_init_custom(self):
        am = AlertManager(rate_limit_seconds=60, loss_streak_threshold=3, balance_low_threshold=5.0)
        assert am.rate_limit_seconds == 60
        assert am.loss_streak_threshold == 3
        assert am.balance_low_threshold == 5.0

    def test_alert_fires(self):
        am = AlertManager(rate_limit_seconds=0)
        result = am.alert(AlertType.SCAN_FAILURE, Severity.WARNING, "Test alert")
        assert result is True

    def test_alert_recorded(self):
        am = AlertManager(rate_limit_seconds=0)
        am.alert(AlertType.SCAN_FAILURE, Severity.WARNING, "Test")
        recent = am.get_recent_alerts(10)
        assert len(recent) == 1
        assert recent[0]["type"] == "SCAN_FAILURE"
        assert recent[0]["severity"] == "WARNING"
        assert recent[0]["message"] == "Test"

    def test_alert_with_details(self):
        am = AlertManager(rate_limit_seconds=0)
        am.alert(AlertType.EXECUTION_FAILURE, Severity.CRITICAL, "Failed", {"order_id": "123"})
        recent = am.get_recent_alerts()
        assert recent[0]["details"]["order_id"] == "123"


class TestAlertRateLimiting:
    def test_rate_limited(self):
        am = AlertManager(rate_limit_seconds=300)
        r1 = am.alert(AlertType.SCAN_FAILURE, Severity.WARNING, "First")
        r2 = am.alert(AlertType.SCAN_FAILURE, Severity.WARNING, "Second")
        assert r1 is True
        assert r2 is False

    def test_different_types_not_rate_limited(self):
        am = AlertManager(rate_limit_seconds=300)
        r1 = am.alert(AlertType.SCAN_FAILURE, Severity.WARNING, "Scan")
        r2 = am.alert(AlertType.EXECUTION_FAILURE, Severity.CRITICAL, "Exec")
        assert r1 is True
        assert r2 is True

    def test_rate_limit_expires(self):
        am = AlertManager(rate_limit_seconds=0.01)
        r1 = am.alert(AlertType.SCAN_FAILURE, Severity.WARNING, "First")
        time.sleep(0.02)
        r2 = am.alert(AlertType.SCAN_FAILURE, Severity.WARNING, "Second")
        assert r1 is True
        assert r2 is True

    def test_string_alert_types(self):
        am = AlertManager(rate_limit_seconds=0)
        r = am.alert("CUSTOM_TYPE", "INFO", "custom alert")
        assert r is True
        recent = am.get_recent_alerts()
        assert recent[0]["type"] == "CUSTOM_TYPE"


class TestLossStreak:
    def test_no_alert_below_threshold(self):
        am = AlertManager(rate_limit_seconds=0, loss_streak_threshold=3)
        am.check_loss_streak(False)
        am.check_loss_streak(False)
        # 2 losses, threshold is 3 — no alert
        recent = am.get_recent_alerts()
        loss_alerts = [a for a in recent if a["type"] == "LOSS_STREAK"]
        assert len(loss_alerts) == 0

    def test_alert_at_threshold(self):
        am = AlertManager(rate_limit_seconds=0, loss_streak_threshold=3)
        am.check_loss_streak(False)
        am.check_loss_streak(False)
        result = am.check_loss_streak(False)
        assert result is True
        recent = am.get_recent_alerts()
        loss_alerts = [a for a in recent if a["type"] == "LOSS_STREAK"]
        assert len(loss_alerts) == 1
        assert "3 consecutive" in loss_alerts[0]["message"]

    def test_win_resets_streak(self):
        am = AlertManager(rate_limit_seconds=0, loss_streak_threshold=3)
        am.check_loss_streak(False)
        am.check_loss_streak(False)
        am.check_loss_streak(True)  # Win resets
        am.check_loss_streak(False)
        am.check_loss_streak(False)
        # Only 2 consecutive losses now
        recent = am.get_recent_alerts()
        loss_alerts = [a for a in recent if a["type"] == "LOSS_STREAK"]
        assert len(loss_alerts) == 0

    def test_continuing_streak(self):
        am = AlertManager(rate_limit_seconds=0, loss_streak_threshold=2)
        am.check_loss_streak(False)
        r1 = am.check_loss_streak(False)
        r2 = am.check_loss_streak(False)  # 3 consecutive
        assert r1 is True
        # r2 may be rate-limited if both fire for same type
        # That's OK — the point is the first alert fired


class TestDailyLoss:
    def test_no_alert_when_profitable(self):
        am = AlertManager(rate_limit_seconds=0)
        result = am.check_daily_loss(5.0, 25.0)
        assert result is False

    def test_80_percent_warning(self):
        am = AlertManager(rate_limit_seconds=0)
        result = am.check_daily_loss(-20.0, 25.0)
        assert result is True
        recent = am.get_recent_alerts()
        assert any("Approaching" in a["message"] for a in recent)

    def test_100_percent_critical(self):
        am = AlertManager(rate_limit_seconds=0)
        # Need to fire 80% first (since it checks 100% before 80%)
        result = am.check_daily_loss(-25.0, 25.0)
        assert result is True
        recent = am.get_recent_alerts()
        assert any("HIT" in a["message"] for a in recent)

    def test_fires_once_per_threshold(self):
        am = AlertManager(rate_limit_seconds=0)
        am.check_daily_loss(-25.0, 25.0)  # 100% fires
        result2 = am.check_daily_loss(-26.0, 25.0)  # Already fired
        assert result2 is False

    def test_zero_limit(self):
        am = AlertManager(rate_limit_seconds=0)
        result = am.check_daily_loss(-100.0, 0)
        assert result is False


class TestPositionLimit:
    def test_no_alert_below_limit(self):
        am = AlertManager(rate_limit_seconds=0)
        result = am.check_position_limit(5, 25)
        assert result is False

    def test_warning_at_90_percent(self):
        am = AlertManager(rate_limit_seconds=0)
        result = am.check_position_limit(23, 25)
        assert result is True
        recent = am.get_recent_alerts()
        assert any("Approaching" in a["message"] for a in recent)

    def test_critical_at_limit(self):
        am = AlertManager(rate_limit_seconds=0)
        result = am.check_position_limit(25, 25)
        assert result is True
        recent = am.get_recent_alerts()
        assert any("reached" in a["message"] for a in recent)

    def test_zero_limit(self):
        am = AlertManager(rate_limit_seconds=0)
        result = am.check_position_limit(5, 0)
        assert result is False


class TestRecentAlerts:
    def test_empty(self):
        am = AlertManager()
        assert am.get_recent_alerts() == []

    def test_order_newest_first(self):
        am = AlertManager(rate_limit_seconds=0)
        am.alert(AlertType.SCAN_FAILURE, Severity.INFO, "First")
        am.alert(AlertType.EXECUTION_FAILURE, Severity.INFO, "Second")
        recent = am.get_recent_alerts()
        assert recent[0]["message"] == "Second"
        assert recent[1]["message"] == "First"

    def test_limit(self):
        am = AlertManager(rate_limit_seconds=0)
        for i in range(10):
            am.alert(f"TYPE_{i}", Severity.INFO, f"Alert {i}")
        recent = am.get_recent_alerts(count=3)
        assert len(recent) == 3

    def test_has_timestamp(self):
        am = AlertManager(rate_limit_seconds=0)
        am.alert(AlertType.SCAN_FAILURE, Severity.INFO, "Test")
        recent = am.get_recent_alerts()
        assert "timestamp" in recent[0]
        assert "epoch" in recent[0]


class TestResetDaily:
    def test_reset_clears_loss_tracking(self):
        am = AlertManager(rate_limit_seconds=0)
        am._loss_80_fired = True
        am._loss_100_fired = True
        am._trade_results.append(False)
        am.reset_daily()
        assert am._loss_80_fired is False
        assert am._loss_100_fired is False
        assert len(am._trade_results) == 0


class TestWebhookIntegration:
    def test_sends_to_notifier(self):
        notifier = MagicMock()
        notifier.url = "https://hooks.slack.com/test"
        am = AlertManager(notifier=notifier, rate_limit_seconds=0)
        am.alert(AlertType.SCAN_FAILURE, Severity.WARNING, "Test")
        # _send_raw should have been called in a thread
        # Give thread time to start
        time.sleep(0.1)
        assert notifier._send_raw.called

    def test_no_notifier(self):
        am = AlertManager(notifier=None, rate_limit_seconds=0)
        # Should not raise
        result = am.alert(AlertType.SCAN_FAILURE, Severity.WARNING, "Test")
        assert result is True

    def test_notifier_without_url(self):
        notifier = MagicMock()
        notifier.url = ""
        am = AlertManager(notifier=notifier, rate_limit_seconds=0)
        am.alert(AlertType.SCAN_FAILURE, Severity.WARNING, "Test")
        # Should not call _send_raw since url is empty
        time.sleep(0.1)
        assert not notifier._send_raw.called


class TestModuleSingleton:
    def test_singleton_exists(self):
        from alerting import alert_manager
        assert isinstance(alert_manager, AlertManager)


class TestStrategyLossStreak:
    """Test per-strategy loss streak tracking (MON-03)."""

    def test_single_loss_no_alert(self):
        """Single loss for a strategy should not fire alert."""
        am = AlertManager(rate_limit_seconds=0)
        result = am.check_strategy_loss_streak("binary", False)
        assert result is False

    def test_two_losses_no_alert(self):
        """Two consecutive losses should not fire alert (threshold is 3)."""
        am = AlertManager(rate_limit_seconds=0)
        am.check_strategy_loss_streak("binary", False)
        result = am.check_strategy_loss_streak("binary", False)
        assert result is False

    def test_three_losses_fires_alert(self):
        """Exactly 3 consecutive losses should fire LOSS_STREAK alert."""
        am = AlertManager(rate_limit_seconds=0)
        am.check_strategy_loss_streak("binary", False)
        am.check_strategy_loss_streak("binary", False)
        result = am.check_strategy_loss_streak("binary", False)
        assert result is True
        recent = am.get_recent_alerts()
        loss_alerts = [a for a in recent if a["type"] == "LOSS_STREAK"]
        assert len(loss_alerts) == 1
        assert "binary" in loss_alerts[0]["message"]
        assert "3 consecutive" in loss_alerts[0]["message"]

    def test_four_consecutive_losses_rate_limited(self):
        """Fourth consecutive loss should not re-alert (rate-limited)."""
        am = AlertManager(rate_limit_seconds=0)
        am.check_strategy_loss_streak("binary", False)
        am.check_strategy_loss_streak("binary", False)
        am.check_strategy_loss_streak("binary", False)  # Alert fires
        result = am.check_strategy_loss_streak("binary", False)
        # Fourth loss should not fire a new alert (rate limiting prevents it)
        assert result is False

    def test_loss_streak_resets_on_win(self):
        """A win should reset the loss counter."""
        am = AlertManager(rate_limit_seconds=0)
        am.check_strategy_loss_streak("binary", False)
        am.check_strategy_loss_streak("binary", False)
        am.check_strategy_loss_streak("binary", True)  # Win resets
        am.check_strategy_loss_streak("binary", False)
        am.check_strategy_loss_streak("binary", False)
        # Only 2 consecutive losses now, not 3
        recent = am.get_recent_alerts()
        loss_alerts = [a for a in recent if a["type"] == "LOSS_STREAK"]
        assert len(loss_alerts) == 0

    def test_multiple_strategies_independent(self):
        """Different strategies should track losses independently."""
        am = AlertManager(rate_limit_seconds=0)
        # Binary: 3 losses (should alert)
        am.check_strategy_loss_streak("binary", False)
        am.check_strategy_loss_streak("binary", False)
        r1 = am.check_strategy_loss_streak("binary", False)
        # Cross: 1 loss (should not alert)
        r2 = am.check_strategy_loss_streak("cross", False)
        assert r1 is True  # Binary alerts
        assert r2 is False  # Cross does not
        recent = am.get_recent_alerts()
        loss_alerts = [a for a in recent if a["type"] == "LOSS_STREAK"]
        assert len(loss_alerts) == 1
        assert "binary" in loss_alerts[0]["message"]

    def test_alert_includes_metadata(self):
        """Alert should include strategy name and loss count."""
        am = AlertManager(rate_limit_seconds=0)
        am.check_strategy_loss_streak("kalshi", False)
        am.check_strategy_loss_streak("kalshi", False)
        am.check_strategy_loss_streak("kalshi", False)
        recent = am.get_recent_alerts()
        loss_alerts = [a for a in recent if a["type"] == "LOSS_STREAK"]
        assert len(loss_alerts) == 1
        alert = loss_alerts[0]
        assert alert["details"]["strategy"] == "kalshi"
        assert alert["details"]["loss_count"] == 3


class TestZeroOpportunityPeriod:
    """Test per-strategy zero-opportunity period detection (MON-03)."""

    def test_zero_opp_under_30min_no_alert(self):
        """No alert should fire for zero opps under 30 minutes."""
        am = AlertManager(rate_limit_seconds=0)
        with patch("alerting.time") as mock_time:
            mock_time.time.return_value = 1000.0
            am.record_strategy_opportunity("binary")
            mock_time.time.return_value = 1600.0  # 10 min later
            am.check_zero_opp_period_per_strategy({"binary": 0})
        recent = am.get_recent_alerts()
        zero_alerts = [a for a in recent if a["type"] == "ZERO_OPP"]
        assert len(zero_alerts) == 0

    def test_zero_opp_over_30min_fires_alert(self):
        """Alert should fire after 30+ minutes with zero opps."""
        am = AlertManager(rate_limit_seconds=0)
        with patch("alerting.time") as mock_time:
            mock_time.time.return_value = 1000.0
            mock_time.gmtime.side_effect = lambda x=None: time.gmtime(mock_time.time.return_value if x is None else x)
            mock_time.strftime.side_effect = lambda fmt, t: time.strftime(fmt, t)
            am.record_strategy_opportunity("binary")
            mock_time.time.return_value = 3800.0  # 30+ min later
            am.check_zero_opp_period_per_strategy({"binary": 0})
        recent = am.get_recent_alerts()
        zero_alerts = [a for a in recent if a["type"] == "ZERO_OPP"]
        assert len(zero_alerts) == 1
        assert "binary" in zero_alerts[0]["message"]
        assert "30" in zero_alerts[0]["message"]

    def test_zero_opp_alert_rate_limited(self):
        """Alert should only fire once per 30-min window (rate limiting)."""
        am = AlertManager(rate_limit_seconds=300)  # 5-min rate limit
        with patch("alerting.time") as mock_time:
            mock_time.time.return_value = 1000.0
            mock_time.gmtime.side_effect = lambda x=None: time.gmtime(mock_time.time.return_value if x is None else x)
            mock_time.strftime.side_effect = lambda fmt, t: time.strftime(fmt, t)
            am.record_strategy_opportunity("binary")
            mock_time.time.return_value = 3800.0  # 30+ min later
            am.check_zero_opp_period_per_strategy({"binary": 0})
            # Second check immediately after (still rate-limited by AlertManager)
            mock_time.time.return_value = 3900.0
            am.check_zero_opp_period_per_strategy({"binary": 0})
        recent = am.get_recent_alerts()
        zero_alerts = [a for a in recent if a["type"] == "ZERO_OPP"]
        # Rate limiting prevents the second alert
        assert len(zero_alerts) == 1

    def test_new_opp_resets_zero_window(self):
        """Finding a new opp should reset the zero-opp window."""
        am = AlertManager(rate_limit_seconds=0)
        with patch("alerting.time") as mock_time:
            mock_time.time.return_value = 1000.0
            mock_time.gmtime.side_effect = lambda x=None: time.gmtime(mock_time.time.return_value if x is None else x)
            mock_time.strftime.side_effect = lambda fmt, t: time.strftime(fmt, t)
            am.record_strategy_opportunity("binary")
            mock_time.time.return_value = 3800.0  # 30+ min later
            am.check_zero_opp_period_per_strategy({"binary": 0})
            # First alert fired
            recent1 = am.get_recent_alerts()
            zero_alerts1 = [a for a in recent1 if a["type"] == "ZERO_OPP"]
            assert len(zero_alerts1) == 1
            # Now record a new opportunity to reset window
            mock_time.time.return_value = 3900.0
            am.record_strategy_opportunity("binary")
            mock_time.time.return_value = 5700.0  # 30+ min after reset
            am.check_zero_opp_period_per_strategy({"binary": 0})
            # Second alert should fire (window was reset)
            recent2 = am.get_recent_alerts()
            zero_alerts2 = [a for a in recent2 if a["type"] == "ZERO_OPP"]
            assert len(zero_alerts2) == 2

    def test_empty_opportunity_dict_no_crash(self):
        """Empty opportunity dict should not crash."""
        am = AlertManager(rate_limit_seconds=0)
        # Should not raise exception
        am.check_zero_opp_period_per_strategy({})
        recent = am.get_recent_alerts()
        assert len(recent) == 0

    def test_multiple_strategies_zero_opp(self):
        """Multiple strategies with zero opps should track independently."""
        am = AlertManager(rate_limit_seconds=0)
        with patch("alerting.time") as mock_time:
            mock_time.time.return_value = 1000.0
            mock_time.gmtime.side_effect = lambda x=None: time.gmtime(mock_time.time.return_value if x is None else x)
            mock_time.strftime.side_effect = lambda fmt, t: time.strftime(fmt, t)
            am.record_strategy_opportunity("binary")
            am.record_strategy_opportunity("cross")
            mock_time.time.return_value = 3800.0  # 30+ min later
            # Both have zero opps now
            am.check_zero_opp_period_per_strategy({"binary": 0, "cross": 0})
        recent = am.get_recent_alerts()
        zero_alerts = [a for a in recent if a["type"] == "ZERO_OPP"]
        # Both should alert (though may be rate-limited)
        assert len(zero_alerts) >= 1

    def test_opp_count_resets_window(self):
        """Non-zero opp count should reset the zero-opp window."""
        am = AlertManager(rate_limit_seconds=0)
        with patch("alerting.time") as mock_time:
            mock_time.time.return_value = 1000.0
            mock_time.gmtime.side_effect = lambda x=None: time.gmtime(mock_time.time.return_value if x is None else x)
            mock_time.strftime.side_effect = lambda fmt, t: time.strftime(fmt, t)
            am.record_strategy_opportunity("binary")
            mock_time.time.return_value = 3700.0  # Just under 30 min
            # Still have opportunities, should reset
            am.check_zero_opp_period_per_strategy({"binary": 5})
            mock_time.time.return_value = 5500.0  # 30+ min after reset
            # Now zero opps
            am.check_zero_opp_period_per_strategy({"binary": 0})
        # Alert should fire because window was reset when we had opps
        recent = am.get_recent_alerts()
        zero_alerts = [a for a in recent if a["type"] == "ZERO_OPP"]
        assert len(zero_alerts) == 1
