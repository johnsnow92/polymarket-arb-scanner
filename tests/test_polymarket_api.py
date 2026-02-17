"""Tests for polymarket_api.py — rate limiter thread safety."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import threading
import time
import types
from unittest.mock import MagicMock, patch

# Mock tenacity before importing polymarket_api — the module uses decorators
# at import time, so we need a mock that passes through the decorated function.
if "tenacity" not in sys.modules:
    _tenacity_mock = types.ModuleType("tenacity")
    # retry() must be a decorator factory that returns the original function unchanged
    _tenacity_mock.retry = lambda **kwargs: (lambda fn: fn)
    _tenacity_mock.stop_after_attempt = lambda *a, **kw: None
    _tenacity_mock.wait_exponential = lambda *a, **kw: None
    _tenacity_mock.retry_if_exception_type = lambda *a, **kw: None
    sys.modules["tenacity"] = _tenacity_mock

# Mock py_clob_client since it may not be installed
for mod in ["py_clob_client", "py_clob_client.client", "py_clob_client.clob_types"]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

from polymarket_api import _rate_limit, _rate_lock
from config import PM_RATE_LIMIT as MIN_REQUEST_INTERVAL


# ---------------------------------------------------------------------------
# _rate_lock existence
# ---------------------------------------------------------------------------


class TestRateLockExists:
    def test_rate_lock_is_a_lock(self):
        assert isinstance(_rate_lock, type(threading.Lock()))

    def test_min_request_interval_value(self):
        assert MIN_REQUEST_INTERVAL == 0.01


# ---------------------------------------------------------------------------
# Single-threaded rate limiting
# ---------------------------------------------------------------------------


class TestRateLimitSingleThread:
    def test_sleeps_when_called_too_quickly(self):
        """Two rapid calls should take at least MIN_REQUEST_INTERVAL apart."""
        _rate_limit()  # prime the last-request timestamp

        start = time.perf_counter()
        _rate_limit()  # should sleep to enforce the interval
        elapsed = time.perf_counter() - start

        # Allow a small tolerance below the interval (timer precision)
        assert elapsed >= MIN_REQUEST_INTERVAL * 0.8, (
            f"Expected at least ~{MIN_REQUEST_INTERVAL}s gap, got {elapsed:.4f}s"
        )

    def test_no_sleep_after_sufficient_pause(self):
        """If enough time passes between calls, _rate_limit should not block."""
        _rate_limit()
        time.sleep(MIN_REQUEST_INTERVAL + 0.05)  # wait longer than interval

        start = time.perf_counter()
        _rate_limit()
        elapsed = time.perf_counter() - start

        # Should return almost immediately (well under the min interval)
        assert elapsed < MIN_REQUEST_INTERVAL, (
            f"Expected near-instant return, got {elapsed:.4f}s"
        )


# ---------------------------------------------------------------------------
# Multi-threaded rate limiting
# ---------------------------------------------------------------------------


class TestRateLimitMultiThread:
    def test_threads_maintain_minimum_interval(self):
        """Launch 5 threads calling _rate_limit simultaneously.

        Verify that the timestamps collected from each thread are all
        separated by at least MIN_REQUEST_INTERVAL.
        """
        timestamps = []
        lock = threading.Lock()

        def worker():
            _rate_limit()
            t = time.perf_counter()
            with lock:
                timestamps.append(t)

        # Prime the rate limiter so the first thread also has to wait
        _rate_limit()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Sort timestamps and check gaps
        timestamps.sort()
        for i in range(1, len(timestamps)):
            gap = timestamps[i] - timestamps[i - 1]
            # Allow 20% tolerance for timer/scheduling jitter
            assert gap >= MIN_REQUEST_INTERVAL * 0.8, (
                f"Gap between request {i - 1} and {i} was {gap:.4f}s, "
                f"expected >= {MIN_REQUEST_INTERVAL * 0.8:.4f}s"
            )

    def test_total_time_scales_with_thread_count(self):
        """N threads should take roughly N * MIN_REQUEST_INTERVAL total."""
        num_threads = 5

        # Prime the rate limiter
        _rate_limit()

        start = time.perf_counter()
        threads = [threading.Thread(target=_rate_limit) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        total = time.perf_counter() - start

        # Total time should be at least (num_threads - 1) * interval
        # (the first thread may not need to wait if enough time passed)
        min_expected = (num_threads - 1) * MIN_REQUEST_INTERVAL * 0.8
        assert total >= min_expected, (
            f"Total time {total:.4f}s too short for {num_threads} threads, "
            f"expected >= {min_expected:.4f}s"
        )
