"""Pre-flight validation script for Railway deployment health.

Checks that the deployed scanner service is reachable, healthy,
and exposing expected endpoints (healthz, status, metrics).
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


# ---------------------------------------------------------------------------
# Color helpers (no deps)
# ---------------------------------------------------------------------------

def _green(text: str) -> str:
    return f"\033[92m{text}\033[0m"


def _red(text: str) -> str:
    return f"\033[91m{text}\033[0m"


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_healthz(base_url: str) -> bool:
    """GET /healthz returns 200."""
    url = f"{base_url.rstrip('/')}/healthz"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def check_status(base_url: str) -> bool:
    """GET /status returns valid JSON with expected fields."""
    url = f"{base_url.rstrip('/')}/status"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            # Expect at least these keys
            return isinstance(data, dict) and "uptime" in data
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return False


def check_metrics(base_url: str) -> bool:
    """GET /metrics returns Prometheus text with key metric names."""
    url = f"{base_url.rstrip('/')}/metrics"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            # Check for at least one expected metric name
            return "trade" in body.lower() or "arb" in body.lower() or "execution" in body.lower()
    except (urllib.error.URLError, OSError):
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Pre-flight check for Railway deployment")
    parser.add_argument("--url", default="http://localhost:8080",
                        help="Base URL of the deployed scanner (default: http://localhost:8080)")
    args = parser.parse_args()

    base_url = args.url
    print(_bold(f"\nPre-flight check: {base_url}\n"))

    checks = [
        ("Health endpoint (/healthz)", check_healthz),
        ("Status endpoint (/status)", check_status),
        ("Metrics endpoint (/metrics)", check_metrics),
    ]

    all_pass = True
    for name, check_fn in checks:
        ok = check_fn(base_url)
        status = _green("PASS") if ok else _red("FAIL")
        print(f"  {status}  {name}")
        if not ok:
            all_pass = False

    print()
    if all_pass:
        print(_green("All checks passed."))
    else:
        print(_red("Some checks failed. Review above."))
        sys.exit(1)


if __name__ == "__main__":
    main()
