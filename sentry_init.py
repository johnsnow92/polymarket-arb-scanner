"""Sentry initialization helper. Import early in any entry point."""

import os
import sentry_sdk


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
    )
