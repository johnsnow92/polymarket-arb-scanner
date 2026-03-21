"""Tests for PlatformCircuitBreaker in rate_limiter.py."""

import sys
import os
import threading
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rate_limiter import PlatformCircuitBreaker


class TestPlatformCircuitBreaker:
    """Tests for PlatformCircuitBreaker behavior."""

    def test_starts_closed(self):
        """Circuit starts in closed state (is_open returns False)."""
        cb = PlatformCircuitBreaker("test_platform")
        assert cb.is_open() is False

    def test_opens_after_fail_limit(self):
        """Circuit opens after fail_limit consecutive failures."""
        cb = PlatformCircuitBreaker("test_platform", fail_limit=3)
        cb.record_failure()
        assert cb.is_open() is False
        cb.record_failure()
        assert cb.is_open() is False
        cb.record_failure()
        assert cb.is_open() is True

    def test_does_not_open_before_fail_limit(self):
        """Circuit stays closed until fail_limit is reached."""
        cb = PlatformCircuitBreaker("test_platform", fail_limit=5)
        for _ in range(4):
            cb.record_failure()
        assert cb.is_open() is False

    def test_auto_resets_after_timeout(self):
        """Circuit auto-resets (returns False) after reset_timeout elapses."""
        with patch("time.time") as mock_time:
            mock_time.return_value = 1000.0
            cb = PlatformCircuitBreaker("test_platform", fail_limit=3, reset_timeout=30.0)
            cb.record_failure()
            cb.record_failure()
            cb.record_failure()
            assert cb.is_open() is True

            # Advance time past reset_timeout
            mock_time.return_value = 1031.0
            assert cb.is_open() is False

    def test_auto_reset_clears_state(self):
        """After auto-reset, failure count resets so circuit can open again."""
        with patch("time.time") as mock_time:
            mock_time.return_value = 1000.0
            cb = PlatformCircuitBreaker("test_platform", fail_limit=3, reset_timeout=30.0)
            cb.record_failure()
            cb.record_failure()
            cb.record_failure()
            assert cb.is_open() is True

            # Advance past timeout — circuit should reset
            mock_time.return_value = 1031.0
            assert cb.is_open() is False

            # After reset, need fail_limit failures again to open
            cb.record_failure()
            assert cb.is_open() is False
            cb.record_failure()
            assert cb.is_open() is False
            cb.record_failure()
            assert cb.is_open() is True

    def test_record_success_closes_circuit(self):
        """record_success resets failure counter and closes an open circuit."""
        cb = PlatformCircuitBreaker("test_platform", fail_limit=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open() is True
        cb.record_success()
        assert cb.is_open() is False

    def test_record_success_resets_counter(self):
        """record_success resets failure count so failures need to reach limit again."""
        cb = PlatformCircuitBreaker("test_platform", fail_limit=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        # Failure count should be 0 now — need 3 more failures to open
        cb.record_failure()
        assert cb.is_open() is False
        cb.record_failure()
        assert cb.is_open() is False
        cb.record_failure()
        assert cb.is_open() is True

    def test_mixed_success_failure_no_premature_open(self):
        """Mixed sequence (fail, success, fail) does not open circuit prematurely."""
        cb = PlatformCircuitBreaker("test_platform", fail_limit=3)
        cb.record_failure()
        cb.record_success()  # resets count
        cb.record_failure()
        assert cb.is_open() is False

    def test_thread_safety(self):
        """Concurrent record_failure calls do not corrupt state."""
        cb = PlatformCircuitBreaker("test_platform", fail_limit=100)
        errors = []

        def worker():
            try:
                for _ in range(10):
                    cb.record_failure()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread safety errors: {errors}"
        # 10 threads * 10 failures each = 100 total
        assert cb._failures == 100

    def test_name_stored(self):
        """Platform name is stored on the instance."""
        cb = PlatformCircuitBreaker("kalshi", fail_limit=3, reset_timeout=30.0)
        assert cb.name == "kalshi"

    def test_default_parameters(self):
        """Default fail_limit=3 and reset_timeout=30.0."""
        cb = PlatformCircuitBreaker("betfair")
        assert cb.fail_limit == 3
        assert cb.reset_timeout == 30.0

    def test_circuit_not_open_before_timeout(self):
        """Circuit remains open before reset_timeout elapses."""
        with patch("time.time") as mock_time:
            mock_time.return_value = 1000.0
            cb = PlatformCircuitBreaker("test_platform", fail_limit=3, reset_timeout=30.0)
            cb.record_failure()
            cb.record_failure()
            cb.record_failure()
            assert cb.is_open() is True

            # Only 29s elapsed — still open
            mock_time.return_value = 1029.0
            assert cb.is_open() is True


class TestRateLimitConfigConstants:
    """Verify new rate limit constants exist in config.py."""

    def test_smarkets_rate_limit_exists(self):
        import config
        assert hasattr(config, "SMARKETS_RATE_LIMIT")
        assert config.SMARKETS_RATE_LIMIT == pytest.approx(0.2)

    def test_sxbet_rate_limit_exists(self):
        import config
        assert hasattr(config, "SXBET_RATE_LIMIT")
        assert config.SXBET_RATE_LIMIT == pytest.approx(0.2)

    def test_matchbook_rate_limit_exists(self):
        import config
        assert hasattr(config, "MATCHBOOK_RATE_LIMIT")
        assert config.MATCHBOOK_RATE_LIMIT == pytest.approx(0.2)

    def test_betfair_rate_limit_exists(self):
        import config
        assert hasattr(config, "BETFAIR_RATE_LIMIT")
        assert config.BETFAIR_RATE_LIMIT == pytest.approx(0.2)
