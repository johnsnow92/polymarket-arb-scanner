"""x402 micropayment client — thin subprocess wrapper around the awal CLI.

Calls `npx awal@2.10.0 x402 pay <url> --json` and returns the parsed response
body. One USDC micropayment is deducted from the agentic wallet on each call.

CRITICAL: Never append query params to the URL. Query params break the x402
payment hash computation, causing "Payment authorized but rejected by server".
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from typing import Any

log = logging.getLogger(__name__)

_SAFE_URL_RE = re.compile(r'^https://[a-zA-Z0-9._/%-]+$')


def x402_pay(url: str, timeout: int = 60) -> dict[str, Any]:
    """Call an x402 endpoint and pay automatically via the awal wallet.

    Args:
        url: Bare endpoint URL — no query string, ever.
        timeout: Subprocess timeout in seconds (x402 round-trips can be slow).

    Returns:
        Parsed JSON from the paid endpoint. awal wraps the response body under
        ``outer['data']``, so the actual payload is at ``result['data']``.

    Raises:
        ValueError: URL fails safety check.
        RuntimeError: Non-zero exit code or non-JSON response.
        subprocess.TimeoutExpired: Payment or fetch took too long.
    """
    if not _SAFE_URL_RE.match(url):
        raise ValueError(f"Unsafe or invalid x402 URL: {url!r}")

    cmd = ['npx', 'awal@2.10.0', 'x402', 'pay', url, '--json']
    log.debug("x402 pay: %s", ' '.join(cmd))

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"awal x402 pay exited {result.returncode}: {result.stderr.strip()}"
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"awal x402 pay returned non-JSON: {result.stdout[:300]!r}"
        ) from exc
