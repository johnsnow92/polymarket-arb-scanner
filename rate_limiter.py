"""Platform circuit breaker for API resilience.

PlatformCircuitBreaker prevents cascading failures when a trading platform
API degrades. After fail_limit consecutive failures, the circuit opens and
further calls are rejected immediately without hitting the API.

After reset_timeout seconds, the circuit resets to half-open and allows
calls to pass through again. If those succeed, it returns to closed state.
"""

import threading
import time

# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


class PlatformCircuitBreaker:
    """Thread-safe circuit breaker for platform API clients.

    Usage in an API client module::

        from rate_limiter import PlatformCircuitBreaker

        _circuit = PlatformCircuitBreaker("betfair", fail_limit=3, reset_timeout=30.0)

        def _call_api(...):
            if _circuit.is_open():
                raise _RateLimitError("Circuit open -- betfair in backoff")
            try:
                result = _inner_call(...)
                _circuit.record_success()
                return result
            except Exception:
                _circuit.record_failure()
                raise

    Args:
        name: Human-readable platform name for logging.
        fail_limit: Number of consecutive failures before circuit opens.
        reset_timeout: Seconds to wait before auto-resetting the circuit.
    """

    def __init__(self, name: str, fail_limit: int = 3, reset_timeout: float = 30.0):
        self.name = name
        self.fail_limit = fail_limit
        self.reset_timeout = reset_timeout
        self._failures: int = 0
        self._open_since: float | None = None
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        """Return True if circuit is open (requests should be rejected).

        Auto-resets after reset_timeout elapses — sets _open_since to None
        and _failures to 0 so the circuit returns to closed state.
        """
        with self._lock:
            if self._open_since is None:
                return False
            elapsed = time.time() - self._open_since
            if elapsed >= self.reset_timeout:
                # Auto-reset: circuit closes, ready for new attempts
                self._failures = 0
                self._open_since = None
                return False
            return True

    def record_success(self):
        """Record a successful API call — resets failure counter and closes circuit."""
        with self._lock:
            self._failures = 0
            self._open_since = None

    def record_failure(self):
        """Record a failed API call — opens circuit when fail_limit is reached."""
        with self._lock:
            self._failures += 1
            if self._failures >= self.fail_limit and self._open_since is None:
                self._open_since = time.time()
