# Testing Patterns

**Analysis Date:** 2026-03-17

## Test Framework

**Runner:**
- pytest (installed via `requirements-dev.txt`)
- Python 3.12 (CI enforces via `.github/workflows/test.yml`)

**Assertion Library:**
- `unittest.mock` (stdlib)
- `pytest.approx()` for floating-point equality

**Run Commands:**
```bash
pytest tests/ -v                              # Run all tests
pytest tests/ -v --tb=short                   # Short traceback format
pytest tests/test_fees.py -v                  # Single test file
pytest tests/test_executor.py::TestBuildLegs -v  # Single test class
pytest tests/test_fees.py::TestPolymarketFee::test_correct_2_percent_calculation -v  # Single test
```

**No pytest.ini or pyproject.toml configuration** — uses all defaults (test discovery pattern: `test_*.py`)

## Test File Organization

**Location:**
- Parallel to source: test files in `tests/` directory
- Pattern: `tests/test_<module>.py` for module `<module>.py`
- Examples:
  - `executor.py` → `tests/test_executor.py`
  - `scans/binary.py` → `tests/test_binary.py` (not nested under tests/scans/)
  - `config.py` → `tests/test_config.py`

**Naming:**
- Test files: `test_*.py`
- Test classes: PascalCase, no `Test` prefix needed (e.g., `class TestBuildLegs:`)
- Test methods: `test_<scenario>` (e.g., `test_binary_legs`, `test_zero_when_sell_equals_buy`)
- Helper methods: leading underscore (e.g., `def _make_args()`, `def _future_iso()`)

**No conftest.py exists** — shared setup uses per-file `autouse` fixtures

## Test Structure

**Pattern: Class-based, all test methods in classes (no module-level test functions)**

```python
"""Tests for <module>.py — brief description."""

import pytest
from unittest.mock import MagicMock, patch
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from module_under_test import Function, Class


class TestFunction:
    """Test suite for Function."""

    def test_scenario_one(self):
        # Arrange
        input_data = ...
        # Act
        result = Function(input_data)
        # Assert
        assert result == expected


class TestClass:
    """Test suite for Class."""

    def test_init(self):
        obj = Class(...)
        assert obj.attr == value
```

**Setup/Teardown:**
- Per-test via `@pytest.fixture(autouse=True)`
- Per-class via `@pytest.fixture` (non-autouse) applied per method
- Module-level: avoided unless necessary

**Mocking pattern for external modules:**

```python
# Before importing executor.py (which imports polymarket_api, kalshi_api, etc.)
@pytest.fixture(autouse=True)
def mock_external_modules():
    """Mock external API modules that may not be installed."""
    mock_modules = {}
    for mod_name in [
        "polymarket_api", "kalshi_api",
        "betfair_api", "smarkets_api", "sxbet_api",
    ]:
        if mod_name not in sys.modules:
            mock_modules[mod_name] = MagicMock()
            sys.modules[mod_name] = mock_modules[mod_name]
    yield
    # Clean up after test
    for mod_name in mock_modules:
        del sys.modules[mod_name]
```

**Reimport within fixture:**
```python
def _import_executor():
    # Force reimport to pick up mocked modules
    if "executor" in sys.modules:
        del sys.modules["executor"]
    from executor import ArbitrageExecutor
    return ArbitrageExecutor

@pytest.fixture
def ArbitrageExecutor():
    return _import_executor()
```

## Mocking

**Framework:** `unittest.mock` (MagicMock, patch, PropertyMock)

**Patterns:**

**1. Fixture-based mocking (preferred):**
```python
@pytest.fixture
def db():
    trade_db = TradeDB(":memory:")
    yield trade_db
    trade_db.close()  # Cleanup

@pytest.fixture
def executor(db):
    pm_trader = MagicMock()
    kalshi_client = MagicMock()
    return ArbitrageExecutor(
        pm_trader=pm_trader,
        kalshi_client=kalshi_client,
        db=db,
        ...
    )
```

**2. Local mocking in test:**
```python
def test_execute_with_trade(self, executor):
    executor.pm_trader.place_order.return_value = {"orderId": "123"}
    executor.execute(opp)
    executor.pm_trader.place_order.assert_called_once()
```

**3. Patch for module-level globals:**
```python
def test_config_override(self):
    with patch("config.MIN_PROFIT_THRESHOLD", 0.01):
        # MIN_PROFIT_THRESHOLD is now 0.01 within this block
        result = some_function()
```

**4. Mock sys.modules for missing dependencies:**
```python
# At module level, before importing the module under test
if "ib_insync" not in sys.modules:
    sys.modules["ib_insync"] = MagicMock()
```

**What to Mock:**
- External APIs: polymarket, Kalshi, Betfair, Gemini, IBKR, etc.
- Network requests: `requests.get()`, `requests.post()`
- Database: `TradeDB` or in-memory SQLite for isolation
- Time-based operations: `datetime.now()` for deterministic date tests

**What NOT to Mock:**
- `fees.py` functions — test with real math
- `config.py` helpers like `_env_float()`, `_env_bool()` — test parsing logic directly
- Core domain logic: `RiskManager.check()`, `ArbitrageExecutor._build_legs()`

## Fixtures and Factories

**Test data pattern — helper functions (not factory classes):**

```python
def _make_args(mode="binary", **kwargs):
    """Build an argparse.Namespace mimicking cli.main()'s parsed args."""
    defaults = {
        "mode": mode,
        "min_profit": None,
        "limit": None,
        "json": False,
        "continuous": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)

def _make_executor(dry_run=True):
    executor = MagicMock()
    executor.dry_run = dry_run
    executor.exec_mode = "semi-auto"
    executor.execute.return_value = True
    return executor
```

**Shared test data (fixtures):**
```python
@pytest.fixture
def sample_opportunity():
    return {
        "type": "Binary",
        "market": "Will Bitcoin reach $100k by 2025?",
        "prices": "Y=0.400 N=0.450",
        "total_cost": "$0.8500",
        "net_profit": 0.0247,
        "net_roi": "2.91%",
        "_token_ids": ["token_yes", "token_no"],
        "_clob_depth": 50.0,
        "_market_key": "MARKET_123",
    }
```

**Location:** Test data defined in the test file where used. No fixtures/ directory.

## Coverage

**Requirements:** Not enforced (no CI coverage gate)

**View coverage:**
```bash
pytest tests/ --cov=. --cov-report=html   # Generates htmlcov/
pytest tests/ --cov=. --cov-report=term   # Terminal report
```

## Test Types

**Unit Tests:**
- Scope: Single function or method in isolation
- Files: `test_fees.py`, `test_config.py`, `test_helpers.py`
- Pattern: Pure functions (fees, config parsing) tested with direct inputs/outputs
- No external API calls; mock all I/O
- Example: `TestPolymarketFee::test_correct_2_percent_calculation()` tests fee math alone

**Integration Tests:**
- Scope: Multiple components working together
- Files: `test_executor.py`, `test_cli.py`, `test_continuous.py`
- Pattern: Real TradeDB (in-memory SQLite), mock API clients
- Example: `test_executor.py` verifies `ArbitrageExecutor` → `RiskManager` → `TradeDB` pipeline
- Database: Always in-memory (`TradeDB(":memory:")`) for isolation

**E2E Tests:**
- Not present in this codebase — CLI is tested via mocked components
- Alternative: `test_integration.py` tests multiple scans + execution in one session (mocked APIs)

## Common Patterns

**Async Testing:**
Not used — no async code in main codebase. `continuous.py` uses `asyncio` but not tested with async/await; mocked at the executor level.

**Floating-Point Assertion:**
```python
def test_kalshi_taker_fee(self):
    fee = kalshi_taker_fee(0.50, 1)
    assert fee == pytest.approx(0.02)  # Tolerates tiny floating-point errors

def test_with_tolerance(self):
    assert actual == pytest.approx(expected, rel=1e-3)  # 0.1% tolerance
```

**Error Testing:**
```python
def test_invalid_env_var(self):
    with patch.dict(os.environ, {"MIN_PROFIT_THRESHOLD": "invalid"}):
        with pytest.raises(ConfigError, match="not a valid float"):
            from config import DEFAULT_MIN_PROFIT

def test_gate_rejects_with_reason(self):
    allowed, reason = risk_manager.check(opp, db)
    assert not allowed
    assert "Daily loss limit" in reason
```

**Fixture Caveat (autouse with sys.modules cleanup):**
```python
@pytest.fixture(autouse=True)
def clean_imports():
    """Clean test isolation — remove only the module under test, not shared libs."""
    yield
    # Only remove the test's specific module, never scans.helpers
    # (other tests need it)
    if "scans.gemini" in sys.modules:
        del sys.modules["scans.gemini"]
```

**Parametrized Tests:**
```python
@pytest.mark.parametrize("price,expected_fee", [
    (0.01, 0.02),   # Min fee
    (0.50, 0.02),   # Max formula
    (0.00, 0.00),   # Boundary
])
def test_kalshi_fee_by_price(price, expected_fee):
    assert kalshi_taker_fee(price, 1) == pytest.approx(expected_fee)
```

## Known Test Issues

**Flaky test:** `test_helpers.py::TestWithinResolutionWindow::test_uses_config_default`
- Cause: Time-sensitive date comparison (compares market resolution to current time)
- Timing: Can fail if test runs at exact boundary of day
- Status: Known but not fixed (rare in practice)

## CI/CD Integration

**GitHub Actions:**
- File: `.github/workflows/test.yml`
- Trigger: Every PR to `master`
- Environment: Python 3.12, runs `pytest tests/ -v`
- Failure: Any test failure or error blocks merge (zero tolerance)
- Install: Both `requirements.txt` and `requirements-dev.txt`

---

*Testing analysis: 2026-03-17*
