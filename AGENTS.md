# AGENTS.md

Instructions for AI coding agents operating in this repository.
For architecture, data flow, and platform details see `CLAUDE.md`.

## Build & Run

```bash
# Install dependencies (no virtual-env required in Docker; use one locally)
pip install -r requirements.txt
pip install -r requirements-dev.txt   # pytest (dev only)

# One-shot scan (all strategies)
python scanner.py

# Continuous mode with WebSocket feeds
python scanner.py --continuous --interval 60

# Single strategy
python scanner.py --mode kalshi
python scanner.py --mode cross-all
python scanner.py --mode event
python scanner.py --mode triangular

# Dry-run is the default; live trading requires explicit flags
python scanner.py --exec-mode full-auto --max-trade 10

# Docker
docker build -t polymarket-arb-scanner .
```

## Testing

```bash
# Run the full suite (1344 tests)
python -m pytest tests/ -v

# Single file
python -m pytest tests/test_fees.py -v

# Single class
python -m pytest tests/test_executor.py::TestExecutor -v

# Single test method
python -m pytest tests/test_fees.py::TestPolymarketFee::test_zero_when_sell_equals_buy -v

# Run with short traceback on failure
python -m pytest tests/ -v --tb=short

# Stop on first failure
python -m pytest tests/ -v -x
```

There is no linter or formatter configured. No `pyproject.toml`, `ruff.toml`,
`.flake8`, or similar config exists. Style is enforced by convention only.

## Code Style

### Python version & typing

- Target **Python 3.10+**.
- Use modern union syntax: `X | None`, `list[float]`, `tuple[bool, str]`.
- Never use `Optional`, `List`, `Dict`, `Tuple` from `typing`.

### Formatting

- ~120 character soft line limit; no hard enforcement.
- 4-space indentation, no tabs.
- Double quotes for strings (dominant convention).

### Logging

- Use `logging` with `%`-style formatting:
  `logger.info("Found %d opps in %s", count, market)`.
- `executor.py` is the sole exception (uses f-strings in log calls).
- Every module creates its own logger: `logger = logging.getLogger(__name__)`.
- Never add bare `except: pass`. Always log at `logger.debug()` minimum.

### Docstrings

- Google-style with `Args:` / `Returns:` sections.
- One-liner docstrings for trivial helpers are fine.

### Data structures

- Opportunities flow as **plain dicts** (not dataclass/TypedDict).
- Internal keys are prefixed with `_` (e.g. `_token_ids`, `_clob_depth`,
  `_market_key`, `_price_a`, `_price_b`).
- Public keys: `type`, `market`, `prices`, `total_cost`, `net_profit`,
  `net_roi`.

### Imports

- Standard library first, then third-party, then local — separated by blank
  lines.
- Relative imports within `scans/` package (`from .helpers import ...`).
- Absolute imports everywhere else (`from fees import net_profit_binary`).

### Section separators

- Use `# ---------------------------------------------------------------------------`
  (75 dashes) to separate logical sections in both source and test files.

## Key Conventions

### scanner.py is a facade

Never add logic to `scanner.py`. It re-exports names from `scans/`, `cli.py`,
`continuous.py`, and `display.py`. Tests patch `scanner.<name>` which hits
these re-exports.

### Two-stage detection

All scan modules follow: mid-price scan (fast) then CLOB refinement
(`_refine_*_with_clob`). The refine step drops candidates that aren't
profitable at real ask prices.

### Adding a new opportunity type

1. Create the scan in `scans/<name>.py` following the two-stage pattern.
2. Add the fee function in `fees.py`.
3. Add a branch in `executor.py:_build_legs()` and a matching
   `_revalidate` case.
4. Wire it into `cli.py:_run_oneshot()` and `continuous.py` if applicable.
5. Add the mode string to `cli.py` argparse choices.

### Adding a new platform pair for cross-platform arbs

Add entries to `_CROSS_FEE_FUNCS` in `scans/cross.py` using
`functools.partial(net_profit_cross_generic, buy_fee, sell_fee)`. All 28
pairs of the 8 trading platforms are already covered.

### Test patterns

- Tests use `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`
  for imports (no package install needed).
- External SDKs are mocked via `sys.modules` stubs before importing the
  module under test (see `test_executor.py` for the pattern).
- Class-based test grouping with section separators between test classes.
- `autouse` fixtures that clean `sys.modules` must only remove the specific
  scan module under test (e.g. `scans.gemini`), never `scans.helpers` or
  `scans.__init__` — this prevents cross-test pollution.

### Thread safety

- `TradeDB` uses a threading lock + SQLite WAL mode.
- Price cache is a plain dict updated from WS threads.
- Per-market locks in continuous mode prevent double execution.

### Config validation

`config.py` uses typed env helpers (`_env_float`, `_env_int`, `_env_bool`)
that raise `ConfigError` (a `ValueError` subclass) on invalid input.
`validate_config()` performs range checks, enum validation, and
cross-field consistency checks on all 79+ config values at startup.

### Config precedence

CLI args > environment variables > defaults in `config.py`.

## Files to never read or commit

`.env`, `.env.*`, `*.pem`, `*.key`, `*credential*`, `secrets/*`

## CI / CD

`.github/workflows/test.yml` runs `pytest` on every PR (Python 3.12,
installs both `requirements.txt` and `requirements-dev.txt`).

## Deployment

Push to `master` triggers CI/CD (`.github/workflows/deploy.yml`):
Docker build -> ECR push -> ECS Fargate deploy.
Manual: `bash infra/deploy.sh`. Health check: `:8080/status`.
