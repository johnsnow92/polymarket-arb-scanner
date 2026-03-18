# Codebase Structure

**Analysis Date:** 2026-03-17

## Directory Layout

```
polymarket-arb-scanner/
├── scanner.py              # CLI facade (re-exports for backward compat)
├── cli.py                  # Main entry point + argument parsing
├── continuous.py           # Continuous mode loop + settlement checks
├── config.py               # Centralized configuration (all env vars)
│
├── scans/                  # Arbitrage detection modules
│   ├── __init__.py         # Re-exports all scan functions
│   ├── helpers.py          # Shared utilities (token IDs, CLOB fetch, scoring)
│   ├── binary.py           # Polymarket binary internal arbs (YES+NO<$1)
│   ├── negrisk.py          # Polymarket multi-outcome arbs (all outcomes)
│   ├── cross.py            # 2-way cross-platform (all 28 platform pairs)
│   ├── multi_cross.py      # Multi-outcome cross-platform (fuzzy event matching)
│   ├── triangular.py       # 3-way cross-platform arbs (union-find grouping)
│   ├── kalshi.py           # Kalshi binary + multi-outcome scans
│   ├── spread.py           # Polymarket/Kalshi bid-ask spreads
│   ├── betfair.py          # Betfair back-all/back-lay arbs
│   ├── smarkets.py         # Smarkets back-all/back-lay arbs
│   ├── sxbet.py            # SX Bet back-all/back-lay arbs
│   ├── matchbook.py        # Matchbook back-all/back-lay arbs
│   ├── gemini.py           # Gemini binary + multi-outcome scans
│   ├── ibkr.py             # IBKR ForecastEx binary scans (BUY-only)
│   ├── stale.py            # Stale price exploitation (slow platforms)
│   ├── resolution.py       # Resolution sniping (near-certain at discount)
│   └── convergence.py      # Cross-platform convergence (outlier→median)
│
├── API Clients             # Platform REST API wrappers
│   ├── polymarket_api.py   # Gamma API + CLOB, Polymarket trader
│   ├── kalshi_api.py       # Kalshi REST API
│   ├── betfair_api.py      # Betfair API + BSP
│   ├── smarkets_api.py     # Smarkets API
│   ├── sxbet_api.py        # SX Bet API
│   ├── matchbook_api.py    # Matchbook API
│   ├── gemini_api.py       # Gemini Predictions API (HMAC auth)
│   ├── ibkr_api.py         # IBKR ForecastEx via TWS socket
│   ├── metaculus_api.py    # Metaculus read-only (signal source)
│   ├── manifold_api.py     # Manifold Markets read-only
│   └── ws_feeds.py         # WebSocket real-time price feeds (Polymarket, Kalshi)
│
├── Execution & Risk        # Trade execution engine
│   ├── executor.py         # ArbitrageExecutor (order placement, fill polling, hedging)
│   ├── risk_manager.py     # RiskManager (position limits, balance, depth gates)
│   ├── db.py               # TradeDB (SQLite persistence, thread-safe WAL)
│   ├── hedger.py           # Partial-fill hedging logic
│   ├── recovery.py         # Orphaned position reconciliation on startup
│   └── position_sizer.py   # Kelly criterion + strategy-aware sizing
│
├── Fees & Matching         # Economic & cross-platform logic
│   ├── fees.py             # Platform-specific fee calculators (14 functions)
│   ├── matcher.py          # Fuzzy market title matching (cross-platform pairing)
│   └── signal_aggregator.py # Multi-source probability consensus
│
├── Monitoring & Signals    # Background monitoring
│   ├── event_monitor.py    # Metaculus divergence signal detection
│   ├── gas_monitor.py      # Polygon gas price monitoring (dynamic fee thresholds)
│   ├── price_tracker.py    # Rolling price tracker with staleness detection
│   ├── market_maker.py     # Market making engine (quote generation, inventory)
│   └── notifier.py         # Async webhook alerts (Slack/Discord/generic)
│
├── Display & Observability # Output & metrics
│   ├── display.py          # Table/JSON output formatting
│   ├── dashboard.py        # HTTP server (/status JSON endpoint)
│   ├── dashboard_ui.py     # Dashboard HTML template
│   ├── metrics.py          # Prometheus-style metrics (counters, gauges, histograms)
│   ├── alerting.py         # Rate-limited structured alerts
│   ├── snapshot.py         # Historical price snapshot recorder (backtesting)
│   └── run_dashboard.py    # Standalone dashboard launcher
│
├── Backtesting & Analysis
│   ├── backtest.py         # Replay engine over recorded snapshots
│   └── tests/              # Full test suite (45+ test files)
│
├── Configuration & Deployment
│   ├── requirements.txt    # Production dependencies
│   ├── requirements-dev.txt # Dev dependencies (pytest)
│   ├── Dockerfile          # Docker build (Python 3.12-slim)
│   ├── docker-compose.yml  # Local dev compose (optional)
│   └── .github/workflows/  # CI/CD (test.yml runs pytest on PR)
│
├── .planning/              # GSD planning documents
│   └── codebase/
│       ├── ARCHITECTURE.md # This file
│       └── STRUCTURE.md    # This file
│
└── tests/                  # Full test suite
    ├── __init__.py
    ├── test_*.py           # 45+ test files, one per module
    └── conftest.py         # (not used; fixtures are per-file autouse)
```


## Directory Purposes

**`scans/`:**
- Purpose: Detect arbitrage opportunities across 20 opportunity types
- Contains: Two-stage scan functions (mid-price → CLOB/API refinement), matchers, aggregators
- Key files: `binary.py`, `cross.py`, `kalshi.py`, `helpers.py` (shared utilities)

**API Clients (`*_api.py`, `ws_feeds.py`):**
- Purpose: Wrap platform REST/WebSocket APIs with auth, retries, rate limiting
- Contains: API methods (get_markets, place_order, get_balance), trader classes, feed subscriptions
- Patterns: Class-based clients with auth in `__init__`, `tenacity` retries on all network calls

**Execution (`executor.py`, `risk_manager.py`, `db.py`):**
- Purpose: Execute opportunities with risk controls and persistence
- Contains: Opportunity validation gates, balance checks, order placement, fill polling, DB persistence
- Thread-safe via: RiskManager (stateless gates), TradeDB (lock + WAL mode), price cache (atomic dict updates)

**Fees & Matching:**
- Purpose: Economic calculations + cross-platform pairing
- `fees.py`: 14+ fee functions (Polymarket 2% on net winnings, Kalshi variable taker, Betfair commission, etc.)
- `matcher.py`: Fuzzy title matching (thefuzz library) to pair markets across platforms
- Applied: During scan refinement AND execution revalidation

**Monitoring:**
- Purpose: Background signal processing and alerts
- `event_monitor.py`: Metaculus consensus vs platform price divergence
- `gas_monitor.py`: Polygon gas price → dynamic profit threshold adjustment
- `notifier.py`: Webhook alerts (Slack, Discord, generic JSON)

**Display & Metrics:**
- Purpose: Output formatting and observability
- `display.py`: Table (tabulate) or JSON output of opportunities
- `dashboard.py`: HTTP server (Flask-like) exposing `/status` JSON for monitoring
- `metrics.py`: Prometheus text exposition (counters, gauges, histograms)


## Key File Locations

**Entry Points:**
- `scanner.py` — CLI facade (calls `cli.main()`)
- `cli.py:main()` — Argument parsing and mode dispatch
- `cli.py:_run_oneshot()` — One-shot scan execution
- `continuous.py:run_continuous()` — Continuous loop with WS feeds

**Configuration:**
- `config.py` — All constants, env var parsing, validation at import time
- `.env` (not committed) — Runtime secrets (API keys, private keys)

**Core Logic:**
- `scans/__init__.py` — Re-exports all scan functions
- `executor.py:ArbitrageExecutor.execute()` — Core trade execution
- `risk_manager.py:RiskManager.check()` — Opportunity gates
- `db.py:TradeDB` — SQLite persistence

**Testing:**
- `tests/test_*.py` — 45+ test files, one per module (no conftest.py, each file has autouse fixtures)
- `tests/test_executor.py` — Mocking pattern for external API modules via sys.modules
- `tests/test_fees.py` — Fee calculator tests (all platforms)


## Naming Conventions

**Files:**
- `scanner.py`, `cli.py`, `config.py` — Orchestration/config (singular, root level)
- `scans/binary.py`, `scans/cross.py` — Scan modules (descriptive, lowercase, plural directory)
- `*_api.py` — Platform API clients (platform_api.py pattern)
- `*_manager.py` — Stateful managers (risk_manager.py, gas_monitor.py)
- `test_*.py` — Test files (underscore prefix, root level in tests/)

**Directories:**
- `scans/` — Scan modules (plural)
- `tests/` — Test files (plural)
- `.planning/` — GSD planning docs (dotdir for exclusion from source)

**Functions:**
- `scan_binary_internal()`, `scan_cross_platform()` — Public scan entry points (scan_X)
- `_refine_binary_with_clob()` — Internal refinement functions (_refine_X)
- `_fetch_clob_for_market()` — Shared helpers (_prefix for internal)
- `net_profit_binary_internal()` — Fee calculator (net_profit_X)
- `get_markets()`, `place_order()` — API methods (verb-first in client classes)

**Variables:**
- `opportunities` — List of opportunity dicts
- `opp` — Single opportunity dict
- `clob_prices` — Dict of CLOB price data
- `markets_by_question` — Lookup dict (platform_by_attribute)
- `_token_ids` — Internal metadata on opportunity dicts (underscore prefix)
- `min_profit` — Configuration value (descriptive lowercase)


## Where to Add New Code

**New Arbitrage Type (e.g., "Spread Betting on Manifold"):**

1. **Scan module**: Create `scans/manifold.py`
   - Function: `scan_manifold_arbs(markets: list[dict], min_profit: float) -> list[dict]`
   - Pattern: Two-stage if possible (REST mid-price → refined via ask prices)
   - Import: Fee function from `fees.py` (or add new one if needed)
   - Return: List of opportunity dicts with standardized keys

2. **Fee calculator**: Add to `fees.py`
   - Function: `net_profit_manifold_arb(prices: ...) -> dict`
   - Return: `{"net_profit": float, "gross_spread": float, "fees": float}`

3. **Wire into executor**: Add case in `executor.py:_build_legs()`
   - Match on `opp["type"]`
   - Convert opportunity dict to execution legs (platform, side, price, etc.)
   - Add corresponding `_revalidate` case for price refresh during execution

4. **CLI integration**:
   - Import in `cli.py` and `continuous.py`
   - Add to argparse choices: `--mode manifold`
   - Add to scan list in `_run_oneshot()` and `run_continuous()`

5. **Tests**: Create `tests/test_manifold_scan.py`
   - Mock Manifold API via `sys.modules` fixture
   - Test scan logic, fee calculation, CLOB refinement

**New Cross-Platform Pair (e.g., Polymarket ↔ New Platform):**

1. **Fee function in `fees.py`**:
   - `net_profit_cross_polymarket_newplatform(buy_price, sell_price, ...) -> dict`
   - Add entry to `scans/cross.py:_CROSS_FEE_FUNCS` dict as `functools.partial(...)`

2. **API client** (if new platform):
   - Create `newplatform_api.py` with class `NewPlatformClient`
   - Methods: `get_markets()`, `get_balance()`, `place_order()`
   - Auth in `__init__`, retries via `tenacity`

3. **Executor support** (if new platform):
   - Add to `executor.py:__init__()` client parameter
   - Add case in `_build_legs()` dispatcher for buy/sell logic
   - Add trader methods like `_place_polymarket_buy()`

4. **Tests**: `tests/test_cross_newplatform.py`
   - Mock both APIs
   - Test fee calculation for all fee combinations

**New Monitoring Signal (e.g., Anomaly Detection):**

1. **Module in root**: `anomaly_detector.py`
   - Class: `AnomalyDetector` with method `detect(opportunities: list[dict]) -> dict`
   - Returns: Dict of signals/alerts

2. **Integration in `continuous.py`**:
   - Initialize detector in `run_continuous()`
   - Call after each scan cycle
   - Feed signals to `notifier.py` for alert dispatch

3. **Tests**: `tests/test_anomaly_detector.py`

**New UI Component (e.g., Dashboard Chart):**

1. **Update `dashboard_ui.py`** — HTML template (add chart div)
2. **Update `dashboard.py`** — HTTP endpoint (add `/metrics` or extend `/status`)
3. **Tests**: Not required for UI-only changes


## Special Directories

**`.planning/`:**
- Purpose: GSD (Get Shit Done) planning documents
- Generated: Yes (by GSD orchestrator)
- Committed: Yes (codebase references them during plan/execute phases)
- Contains: ARCHITECTURE.md, STRUCTURE.md, and phase-specific docs

**`.github/workflows/`:**
- Purpose: CI/CD (GitHub Actions)
- Contains: `test.yml` — runs pytest on every PR, Python 3.12
- Committed: Yes
- Auto-triggered: On PR/push to master

**`tests/`:**
- Purpose: Full test suite (45+ files)
- Generated: No
- Committed: Yes
- Fixtures: Per-file `autouse` fixtures (no conftest.py — see testing patterns)

**`~/ (hidden directory, generated by development)**
- Purpose: Development artifacts
- Generated: Yes (by local testing)
- Committed: No (.gitignore)


## File Organization Principles

1. **Flat root level** — Only orchestration (scanner.py, cli.py, continuous.py, config.py) and re-exports at root
2. **Scan modules in `scans/` package** — All detection logic grouped, re-exported via `__init__.py`
3. **API clients named explicitly** — `*_api.py` pattern makes platform clear
4. **One-module-one-file** — Each module has its own file; no large mega-files except executor (necessary for integration complexity)
5. **Support modules in root** — fees.py, matcher.py, recovery.py, etc. (used by multiple layers)
6. **Tests mirror source structure** — `tests/test_*.py` for each source module

---

*Structure analysis: 2026-03-17*
