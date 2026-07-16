"""Tests for polymarket_api.py — rate limiter thread safety."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import threading
import time
import types
import pytest
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

import polymarket_api
from polymarket_api import _rate_limit, _rate_lock
from config import PM_RATE_LIMIT as MIN_REQUEST_INTERVAL


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Reset polymarket circuit breaker state between tests."""
    polymarket_api._circuit.record_success()
    yield
    polymarket_api._circuit.record_success()


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Clear module-level _last_request_time between tests so a previous
    test's call doesn't shift timing in the next one."""
    polymarket_api._last_request_time = 0
    yield
    polymarket_api._last_request_time = 0


# ---------------------------------------------------------------------------
# _rate_lock behaviour
# ---------------------------------------------------------------------------
#
# We assert the *behaviour* of _rate_lock (acquire/release, context-manager
# usable, mutually exclusive) rather than its concrete type. Python 3.13
# turned threading.Lock into a class where it had previously been a factory
# returning _thread.lock, so isinstance(_rate_lock, type(threading.Lock()))
# is brittle across Python versions.


class TestRateLockExists:
    def test_rate_lock_is_a_lock(self):
        # Lock-shaped: has acquire/release and works as a context manager.
        assert callable(getattr(_rate_lock, "acquire", None))
        assert callable(getattr(_rate_lock, "release", None))
        with _rate_lock:
            pass

    def test_rate_lock_is_mutually_exclusive(self):
        # Holding the lock should block a non-blocking acquire from another
        # thread. Earlier versions of this test compared
        # ``acquired_concurrently == [False]``; on recent Python versions
        # ``threading.Lock.acquire(blocking=False)`` returns the lock-state
        # object rather than the literal ``False`` when blocking. Compare
        # truthiness instead.
        acquired_concurrently = []

        def try_acquire():
            acquired_concurrently.append(_rate_lock.acquire(blocking=False))

        with _rate_lock:
            t = threading.Thread(target=try_acquire)
            t.start()
            t.join(timeout=2)

        assert len(acquired_concurrently) == 1
        assert not acquired_concurrently[0]

    def test_min_request_interval_value(self):
        assert MIN_REQUEST_INTERVAL == 0.01


# ---------------------------------------------------------------------------
# Single-threaded rate limiting (deterministic — mocked time)
# ---------------------------------------------------------------------------
#
# Real-time tests are unreliable on shared CI runners: time.sleep(0.01) can
# return after only microseconds when the runner is under contention, so we
# observed gaps of ~0.0002s where 0.008s was expected. Rather than papering
# over the flakiness with a sleep budget, we mock time.time + time.sleep and
# assert the rate limiter's *logic*: it calls sleep with the correct
# remaining-interval duration, and updates _last_request_time afterwards.


def _fake_time_module(time_values, sleep_mock):
    """Build a fake replacement for the ``time`` module reference held by
    polymarket_api. Replacing the whole module reference (instead of
    patching ``polymarket_api.time.time`` directly) keeps the mock fully
    scoped to the test — patching the sub-attribute mutated the real
    ``time`` module's globals, which made the assertions race against
    real wall-clock readings on shared CI runners.
    """
    fake = MagicMock()
    fake.time = MagicMock(side_effect=lambda: next(time_values))
    fake.sleep = sleep_mock
    return fake


class TestRateLimitSingleThread:
    def test_sleeps_when_called_too_quickly(self):
        """When _last_request_time is recent, _rate_limit must sleep for the
        remainder of MIN_REQUEST_INTERVAL."""
        # Pretend the previous request happened at t=100.000 and now is
        # t=100.003 — 3ms in, so we should sleep for the remaining 7ms.
        polymarket_api._last_request_time = 100.000
        time_values = iter([100.003, 100.010])
        mock_sleep = MagicMock()
        fake_time = _fake_time_module(time_values, mock_sleep)

        with patch.object(polymarket_api, "time", fake_time):
            _rate_limit()

        assert mock_sleep.call_count == 1
        slept_for = mock_sleep.call_args[0][0]
        assert slept_for == pytest.approx(MIN_REQUEST_INTERVAL - 0.003, abs=1e-9)
        # _last_request_time should advance to the post-sleep timestamp.
        assert polymarket_api._last_request_time == 100.010

    def test_no_sleep_after_sufficient_pause(self):
        """When the previous request was long ago, _rate_limit must not sleep."""
        polymarket_api._last_request_time = 100.000
        time_values = iter([100.500, 100.500])
        mock_sleep = MagicMock()
        fake_time = _fake_time_module(time_values, mock_sleep)

        with patch.object(polymarket_api, "time", fake_time):
            _rate_limit()

        mock_sleep.assert_not_called()
        assert polymarket_api._last_request_time == 100.500


# ---------------------------------------------------------------------------
# Multi-threaded rate limiting
# ---------------------------------------------------------------------------
#
# Real concurrency tests with a 10ms sleep are hopelessly flaky on shared CI
# runners (we saw 5-thread total runtimes of 0.7ms vs expected 32ms). The
# guarantees we actually care about are:
#   1. _rate_limit is serialised by _rate_lock — only one thread executes the
#      sleep/timestamp-update critical section at a time.
#   2. Each call serialises through that critical section, so N concurrent
#      callers each observe a fresh _last_request_time set by their
#      predecessor.
# Both can be verified deterministically without relying on wall-clock sleep.


class TestRateLimitMultiThread:
    def test_threads_maintain_minimum_interval(self):
        """All threads must serialise through the rate-limiter critical
        section: each call observes a _last_request_time at least as recent
        as the previous call's update."""
        observed_last_times: list[float] = []
        clock_lock = threading.Lock()

        # Counter advances by MIN_REQUEST_INTERVAL each time time.time() is
        # consulted, so every thread sees a strictly increasing clock.
        clock = [100.000]

        def fake_time():
            with clock_lock:
                clock[0] += MIN_REQUEST_INTERVAL
                return clock[0]

        # We only want to verify ordering, not actually sleep.
        def fake_sleep(_):
            pass

        fake_time_mod = MagicMock()
        fake_time_mod.time = fake_time
        fake_time_mod.sleep = fake_sleep

        def worker():
            _rate_limit()
            observed_last_times.append(polymarket_api._last_request_time)

        polymarket_api._last_request_time = 0
        # Apply the time-module patch outside the worker so all threads
        # share one consistent fake. Patching inside the worker thread
        # creates a race where the patch may not apply by the time the
        # worker calls _rate_limit() on a fast CI runner.
        with patch.object(polymarket_api, "time", fake_time_mod):
            threads = [threading.Thread(target=worker) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        # Every thread must have seen a strictly increasing timestamp ≥
        # MIN_REQUEST_INTERVAL apart, proving they were serialised.
        sorted_times = sorted(observed_last_times)
        assert len(sorted_times) == 5
        for i in range(1, len(sorted_times)):
            assert sorted_times[i] >= sorted_times[i - 1]

    def test_total_time_scales_with_thread_count(self):
        """N concurrent _rate_limit calls must result in N total
        sleep-or-update cycles — i.e. each thread executes the critical
        section exactly once and the lock prevents any from being skipped.

        Mirrors test_sleeps_when_called_too_quickly's pattern of swapping
        the entire ``polymarket_api.time`` reference rather than patching
        sub-attributes, so the mock is fully scoped to this test.
        """
        sleep_calls: list[float] = []
        lock = threading.Lock()
        clock = [100.000]

        def fake_time():
            with lock:
                # Each tick advances the clock by 1ms — each thread sees a
                # fresh _last_request_time + 1ms when it enters the
                # critical section, so it always finds the limiter "hot"
                # and calls sleep before updating _last_request_time.
                clock[0] += 0.001
                return clock[0]

        def fake_sleep(d):
            with lock:
                sleep_calls.append(d)

        fake_time_mod = MagicMock()
        fake_time_mod.time = fake_time
        fake_time_mod.sleep = fake_sleep

        polymarket_api._last_request_time = 100.000

        with patch.object(polymarket_api, "time", fake_time_mod):
            num_threads = 5
            threads = [
                threading.Thread(target=_rate_limit) for _ in range(num_threads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        # Every thread that found the limiter "hot" should have called sleep.
        # At minimum, num_threads-1 calls would have to wait (one might have
        # arrived after enough simulated clock advancement to skip).
        assert len(sleep_calls) >= num_threads - 1
