"""Sentry initialization helper. Import early in any entry point."""

import os
import re
import sentry_sdk

# Variable names that look like credentials. If local-variable capture is
# ever re-enabled, before_send drops any frame-local whose name matches —
# venue API keys/secrets are in scope during request signing and must never
# ship to Sentry.
_SECRET_VAR_PATTERN = re.compile(
    r"(api[_-]?key|apikey|secret|token|passw|credential|private[_-]?key|"
    r"auth|signature|seed|mnemonic)",
    re.IGNORECASE,
)

_SCRUBBED = "[Scrubbed]"


def _scrub_event(event, hint):
    """before_send hook: strip secret-shaped local variables from stack frames.

    Defense-in-depth behind ``include_local_variables=False`` — if locals ever
    get re-enabled, obvious secret-shaped vars are still redacted.

    Args:
        event: Mutable Sentry event payload.
        hint: Sentry capture hint; accepted for the before-send hook contract.

    Returns:
        The event payload with credential-shaped frame locals redacted.
    """
    for exc in (event.get("exception", {}) or {}).get("values", []) or []:
        frames = (exc.get("stacktrace", {}) or {}).get("frames", []) or []
        for frame in frames:
            frame_vars = frame.get("vars")
            if not frame_vars:
                continue
            for name in list(frame_vars):
                if _SECRET_VAR_PATTERN.search(name):
                    frame_vars[name] = _SCRUBBED
    return event


def init_sentry() -> None:
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return

    env = os.environ.get("ENV", os.environ.get("NODE_ENV", "development"))
    sentry_sdk.init(
        dsn=dsn,
        environment=env,
        traces_sample_rate=0.2 if env == "production" else 1.0,
        send_default_pii=False,
        # Never capture exception-frame locals: venue credentials are in
        # scope during order signing and would ship to Sentry otherwise.
        include_local_variables=False,
        before_send=_scrub_event,
    )
