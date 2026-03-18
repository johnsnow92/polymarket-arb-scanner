# Coding Conventions

**Analysis Date:** 2026-03-17

## Naming Patterns

**Files:**
- Lowercase with underscores: `polymarket_api.py`, `risk_manager.py`, `scans/binary.py`
- Test files: `test_<module>.py` in `tests/` directory (parallel to source)
- API clients: `<platform>_api.py` (e.g., `kalshi_api.py`, `gemini_api.py`, `ibkr_api.py`)
- Scan modules: `scans/<type>.py` (e.g., `scans/binary.py`, `scans/cross.py`)

**Functions:**
- Lowercase with underscores: `fetch_all_markets()`, `net_profit_binary_internal()`, `_rate_limit()`
- Private/internal functions: leading underscore `_refine_binary_with_clob()`, `_env_float()`, `_days_to_resolution()`
- Two-word scan operations: stage 1 (mid-price), stage 2 refinement via `_refine_<type>_with_clob()`

**Variables:**
- Lowercase with underscores: `min_profit`, `daily_pnl`, `clob_results`, `fetch_futures`
- Dict keys: flat strings or underscore-separated: `"net_profit"`, `"_token_ids"`, `"_clob_depth"`, `"_market_key"`
- Internal dict keys: leading underscore prefix: `"_token_ids"`, `"_clob_refined"`, `"_platform_a"` (distinguishes computed metadata from API response data)

**Types:**
- Classes: PascalCase: `ArbitrageExecutor`, `TradeDB`, `RiskManager`, `PolymarketTrader`, `KalshiClient`
- Exceptions: PascalCase ending in `Error`: `ConfigError`, `_RateLimitError`
- Union syntax: modern `X | None` (not `Optional[X]`), `list[dict]` (not `List[Dict]`)

## Code Style

**Formatting:**
- No linter or formatter configured. Style enforced by convention only.
- **Double quotes for all strings** (not single quotes)
- **Soft line limit ~120 characters** (not strict)
- **Two blank lines** between top-level functions/classes
- **One blank line** between methods in a class

**Imports:**
- Standard library first
- Third-party packages second
- Local imports last
- Relative imports within `scans/` package: `from .helpers import ...`
- Absolute imports elsewhere: `from executor import ArbitrageExecutor`

**Python version:**
- Python 3.10+ required
- Modern union syntax: `X | None`, `list[float]`, `tuple[bool, str]`
- Never use `Optional`, `List`, `Dict`, `Tuple` from `typing`

**Section separators:**
- `# ---------------------------------------------------------------------------` (75 dashes) between major logical sections
- Used in module docstrings and between classes/function groups

## Logging

**Framework:** Python `logging` module (stdlib)

**Pattern:**
```python
import logging

logger = logging.getLogger(__name__)

# In code:
logger.info("Found %d opportunities in %s", count, market_name)
logger.warning("Rate limited on endpoint: %s", url)
logger.error("Trade failed for %s: %s", ticker, error_msg)
logger.debug("Internal state: %s", state_dict)
```

**Style:**
- **`%`-style formatting only** (not f-strings, except in `executor.py`)
- Log level progression: DEBUG (state snapshots) → INFO (milestones) → WARNING (recoverable issues) → ERROR (trade failures)
- Exception handling in catch blocks: `logger.error("Action failed: %s", e)` with exception in message

**Special case:**
- `executor.py` is the sole exception using f-strings for logs (e.g., `logger.info(f"Trade {opp_id}: ...")`)

## Comments

**When to comment:**
- Complex profit calculations in `fees.py` — explain the formula
- Multi-stage scan logic (mid-price vs CLOB refinement)
- Non-obvious platform-specific behavior (e.g., Kalshi fee caps, IBKR 5-second order rate limit)
- Risk gate logic with detailed reasoning

**DocStrings/JSDoc:**
- Public functions: always include docstring
- Private functions (leading `_`): docstring if non-obvious
- Module-level: always include brief docstring describing the module's purpose
- Format: Google-style docstrings with Args, Returns, Raises sections

**Example:**
```python
def net_profit_binary_internal(yes_price: float, no_price: float) -> dict:
    """Calculate net profit for a Polymarket binary arbitrage.

    Buy YES + NO. One always pays $1.00.
    Profit = $1.00 - (yes_price + no_price) - fees.

    Args:
        yes_price: Price of YES outcome (0-1)
        no_price: Price of NO outcome (0-1)

    Returns:
        Dict with keys: gross_spread, fees, net_profit
    """
```

## Error Handling

**Patterns:**
- **Three custom exceptions across codebase:**
  - `ConfigError(ValueError)` in `config.py` — invalid env var values
  - `_RateLimitError(Exception)` in `polymarket_api.py` and `kalshi_api.py` — HTTP 429 responses (triggers retry)

**Retry strategy:**
- `tenacity` library with exponential backoff for transient errors
- Decorator pattern: `@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10), ...)`
- Applied to: `_get_with_retry()` in API clients, platform balance/order calls
- Never silent failures: all exceptions are either logged or re-raised

**In scanners:**
- Mid-price phase: failures logged at WARNING level, returns empty list
- CLOB refinement: `try/except` with fallback (keep candidate if CLOB unavailable)
- `_within_resolution_window()` returns False on unparseable dates (conservative: skip unknown markets)

**In executor:**
- Risk checks return `(allowed: bool, reason: str)` tuple (non-throwing gates)
- Trade failures logged at ERROR level with market/ticker context
- `_failed_cooldowns` dict tracks markets to avoid retry loops

## Function Design

**Size guideline:**
- Target <50 lines per function
- Two-stage scan functions explicitly split into stage 1 (fetch) and stage 2 (refine)
- Example: `scan_binary_internal()` calls `_refine_binary_with_clob()` in sequence

**Parameters:**
- Positional: required parameters (e.g., `markets: list[dict]`, `min_profit: float`)
- Keyword: optional/configurable (e.g., `price_cache: dict | None = None`, `max_days: int = None`)
- Avoid >5 parameters; use a dict for related config params
- Example: `ArbitrageExecutor.__init__()` takes 18 params but splits between positional (essential clients) and keyword (flags/limits)

**Return values:**
- Scan functions: `list[dict]` (opportunities with standardized keys)
- Executors: `bool` (True if executed, False if skipped)
- Gates: `(bool, str)` tuple (allowed and reason)
- Calculations: `dict` with labeled outputs (e.g., `{"gross_spread": 0.05, "fees": 0.01, "net_profit": 0.04}`)

**Opportunity dict standardization:**
- Core keys: `type`, `market`, `prices`, `total_cost`, `net_profit`, `net_roi`
- Internal keys (leading `_`): `_token_ids`, `_clob_depth`, `_market_key`, `_platform_a`, `_token_id`, `_kalshi_ticker`
- Platform-specific nested keys: `_kalshi_yes`, `_kalshi_no`, `_kalshi_tickers`, `_kalshi_prices`

## Module Design

**Exports:**
- Scan modules: public functions + private stage 2 refiners (e.g., `_refine_binary_with_clob` in `__all__`)
- API clients: public fetch/trade methods; internal `_rate_limit()` and retry decorators
- Barrel file: `scans/__init__.py` exports all public scan functions and helpers

**Barrel files:**
- `scans/__init__.py` — imports and re-exports all scan functions for use in `cli.py`
- Pattern: `from scans.binary import scan_binary_internal, _refine_binary_with_clob`

**Config precedence:**
- CLI args > environment variables > defaults in `config.py`
- Example: `--min-profit 0.01` overrides `MIN_PROFIT_THRESHOLD` env var, which overrides `DEFAULT_MIN_PROFIT` default

## Opportunity Type Flow

**Scanner → Executor contract:**
1. Scanner produces dict with `type` key matching executor's `_build_legs()` dispatcher
2. Standard types: `"Binary"`, `"NegRisk (4 outcomes)"`, `"KalshiBinary"`, `"KalshiMulti (3 outcomes)"`, `"Cross"`, `"Spread"`, etc.
3. Executor's `_build_legs(opp, trade_size)` converts to execution legs by switching on `opp["type"]`
4. Must add corresponding `_revalidate_<type>()` case for price revalidation during execution

---

*Convention analysis: 2026-03-17*
