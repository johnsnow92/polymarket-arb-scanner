# Architecture

**Analysis Date:** 2026-03-17

## Pattern Overview

**Overall:** Three-layer pipeline with thin orchestration shell — detection → validation → execution.

**Key Characteristics:**
- **Two-stage detection**: Mid-price REST scan (fast) → CLOB ask refinement (accurate)
- **Opportunity dict flow**: Opportunities pass through system as plain dicts with standardized keys (`type`, `market`, `prices`, `net_profit`, etc.)
- **Multi-platform support**: 8 trading platforms (Polymarket, Kalshi, Betfair, Smarkets, SX Bet, Matchbook, Gemini, IBKR) with pluggable API clients
- **Risk-first execution**: Every opportunity passes through RiskManager gates before execution
- **Real-time feeds**: WebSocket subscriptions to Polymarket and Kalshi with O(1) opportunity lookup via OpportunityIndex
- **Graceful degradation**: Missing or slow platforms don't block scanner; WS cache falls back to REST API


## Layers

**Orchestration Layer:**
- Purpose: CLI entry point, mode dispatch, parallel initialization
- Location: `scanner.py` (facade), `cli.py` (actual entry point), `continuous.py` (loop management)
- Contains: Argument parsing, client initialization, mode selection (one-shot vs continuous), settlement checks
- Depends on: All other layers
- Used by: Command line, deployment containers

**Scan Layer:**
- Purpose: Detect arbitrage opportunities using REST APIs
- Location: `scans/` package (14 scan modules + `helpers.py`)
- Contains: Two-stage scanners (mid-price → CLOB refinement), type-specific matchers (cross-platform, multi-outcome), signal aggregators (event divergence, convergence)
- Depends on: Platform API clients (`*_api.py`), fee calculators (`fees.py`), market matchers (`matcher.py`)
- Used by: `cli.py` one-shot, `continuous.py` loop

**Execution Layer:**
- Purpose: Trade execution with risk controls and persistence
- Location: `executor.py` (main), `risk_manager.py` (gates), `db.py` (persistence)
- Contains: Opportunity validation, balance checks, position sizing, order placement, fill polling, partial-fill hedging
- Depends on: Platform traders (PolymarketTrader, KalshiClient, etc.), RiskManager, TradeDB
- Used by: `cli.py`, `continuous.py`, recovery routines

**Support Modules:**
- Platform APIs: `*_api.py` (9 clients) + `ws_feeds.py` (real-time price feeds)
- Fees: `fees.py` (platform-specific profit calculators)
- Matching: `matcher.py` (fuzzy title matching for cross-platform pairs)
- Risk: `risk_manager.py` (position limits, balance checks, depth gates)
- Persistence: `db.py` (SQLite, thread-safe WAL mode)
- Monitoring: `event_monitor.py` (Metaculus signal detection), `gas_monitor.py` (Polygon gas thresholds), `recovery.py` (orphaned position reconciliation)
- Output: `display.py` (table/JSON formatting), `dashboard.py` (HTTP status endpoint)


## Data Flow

**One-Shot Mode** (`cli.py:_run_oneshot`):

1. **Parallel fetch** — ThreadPoolExecutor fetches:
   - Polymarket: all active markets + events
   - Kalshi: all active markets
   - Event data from Metaculus (if enabled)

2. **Parallel scan** — ThreadPoolExecutor runs:
   - Polymarket internal: `scan_binary_internal()`, `scan_negrisk_internal()`
   - Kalshi single-platform: `scan_kalshi_binary()`, `scan_kalshi_multi()`
   - Polymarket-specific: `scan_spread_polymarket()`
   - Exchange back-all/back-lay: Betfair, Smarkets, SX Bet, Matchbook

3. **Sequential cross-platform** (requires data from step 1):
   - `scan_cross_platform()` — all 28 pairs of 8 platforms
   - `scan_cross_all()` — alternative matcher
   - `scan_triangular()` — 3-way cross-platform via union-find

4. **Advanced strategies** (signal-based):
   - `scan_event_divergence()` — Metaculus consensus vs platform misprice
   - `scan_stale_prices()` — slow-updating platforms
   - `scan_resolution_snipes()` — near-certain outcomes at discount
   - `scan_convergence()` — outlier platform vs median price

5. **Multi-outcome cross-platform**:
   - `scan_multi_cross()` — cheapest YES per outcome, fuzzy event matching

6. **Sort & filter**:
   - Rank by `capital_efficiency_score()` (ROI × depth / capital)
   - Filter dust trades below `MIN_PROFIT_AMOUNT`

7. **Display & execute**:
   - `display_results()` — table or JSON
   - For each opportunity:
     - RiskManager.check() → allowed?
     - ArbitrageExecutor.execute() → dry-run or live

**Continuous Mode** (`continuous.py:run_continuous`):

1. **Initialize state**:
   - OpportunityIndex (maps platform/ticker → opportunities)
   - FeedManager (WebSocket connections to Polymarket + Kalshi)
   - Price cache (dict, updated by WS callbacks, 60s TTL)

2. **Settlement check** (hourly):
   - Poll settled markets, record realized P&L

3. **Re-scan loop** (default 30s interval):
   - Same as one-shot, rebuild OpportunityIndex

4. **WS price update callback**:
   - On new price: lookup related opportunities via OpportunityIndex
   - Revalidate profit (WS prices more current than REST)
   - Execute if profitable and opportunity locked per-market
   - Update price cache for next REST scan

5. **Graceful shutdown**:
   - SIGINT/SIGTERM → close WS, wait for pending executions, exit


**State Management:**

- **Price cache** (`dict[str, dict]`): Shared between WS feeds and REST scanner, 60s TTL, keyed by platform/token
- **Opportunity index** (`OpportunityIndex`): Rebuilt each scan cycle, maps (platform, ticker) → list of opportunities for fast WS lookup
- **DB state** (`TradeDB`): All-time history + current open positions, thread-safe via WAL mode + lock
- **Balance cache** (`executor._balance_cache`): Per-opportunity-type cache, ~10s TTL, invalidated after trades
- **Failed-trade cooldown** (`executor._failed_cooldowns`): Per-market/ticker, prevents catastrophic loops on bogus opportunities


## Key Abstractions

**Opportunity Dict:**
- Purpose: Standardized representation of a detected arbitrage opportunity
- Examples: `scans/binary.py`, `scans/cross.py`, `scans/kalshi.py` all build these
- Pattern: Plain dict with keys: `type`, `market`, `prices`, `total_cost`, `net_profit`, `net_roi`, internal keys prefixed with `_`
- Standardized fields:
  ```python
  {
      "type": "Binary",                    # Opportunity class
      "market": "Will X happen by date?",  # Market question
      "prices": "Y=0.40 N=0.45",          # Entry prices
      "total_cost": "$0.85",               # Capital required
      "net_profit": 0.0247,                # Absolute profit ($)
      "net_roi": "2.91%",                  # Return on investment
      "_token_ids": ["id1", "id2"],        # CLOB token IDs (internal)
      "_clob_depth": 15.5,                 # Available liquidity (internal)
      "_market_key": "market:123",         # Market ID (internal)
  }
  ```

**Platform API Client:**
- Purpose: Wraps REST API with auth, retries, rate limiting, proxy support
- Examples: `polymarket_api.PolymarketTrader`, `kalshi_api.KalshiClient`, `gemini_api.GeminiClient`
- Pattern: Class with methods like `get_markets()`, `place_order()`, `get_balance()`, init handles auth setup
- Shared features: `tenacity` retry decorator (stop_after_attempt=3, exponential backoff), session-level HTTP adapters for pooling

**Fee Calculator:**
- Purpose: Platform-specific net profit after fees/gas
- Examples: `fees.net_profit_binary_internal()`, `fees.net_profit_kalshi_binary()`, `fees.net_profit_cross_platform()`
- Pattern: Takes input prices, returns dict with `{"net_profit": float, "gross_spread": float, "fees": float}`
- Applied: During scan refinement AND during execution revalidation

**Scan Module:**
- Purpose: Detect opportunities of a specific type (binary, cross-platform, etc.)
- Examples: `scans/binary.py`, `scans/cross.py`, `scans/gemini.py`
- Pattern: Two-stage function pair:
  1. `scan_X_internal()` — mid-price REST scan, returns candidates
  2. `_refine_X_with_clob()` — ask-price refinement, drops unprofitable, attaches depth
- Both stages return list of opportunity dicts

**RiskManager:**
- Purpose: Pure gate — returns (allowed, reason) for each opportunity
- Examples: `risk_manager.RiskManager.check()`
- Pattern: Stateless checks (daily P&L, open positions, balance, depth) on a single opportunity + DB state
- Gates are evaluated in order; first failure prevents execution

**TradeDB:**
- Purpose: Thread-safe SQLite persistence for opportunities, trades, positions
- Examples: `db.TradeDB`
- Pattern: All operations lock + execute, WAL mode allows concurrent reads while writing
- Tables: `opportunities`, `trades`, `positions`, `partial_fills`


## Entry Points

**CLI**:
- Location: `scanner.py` (calls `cli.main()`)
- Triggers: `python scanner.py [--mode MODE] [--continuous] [--dry-run | --exec-mode MODE]`
- Responsibilities: Argument parsing, client initialization, mode dispatch to `_run_oneshot()` or `run_continuous()`

**Continuous Mode**:
- Location: `continuous.py:run_continuous()`
- Triggers: `--continuous` flag
- Responsibilities: Periodic re-scans, WS feed management, settlement checks, dashboard state updates

**Programmatic (Testing)**:
- Imports: Tests directly import scan functions (`scans.binary.scan_binary_internal()`) and execute them
- Pattern: Modules are designed to be composable; tests mock platform APIs via `sys.modules` stubs


## Error Handling

**Strategy:** Fail-forward with logging; bad opportunities are skipped, platform outages don't block scanner.

**Patterns:**

- **API retries** (`polymarket_api`, `kalshi_api`): `tenacity` decorator with 3 attempts, exponential backoff, retry on 429/timeout/connection error
- **Missing platform data**: Scan returns `[]` (empty opportunities), scanner continues
- **CLOB fetch failures**: Refinement logs warning, keeps candidate if CLOB unavailable (conservative)
- **WS disconnects** (`continuous.py`): FeedManager auto-reconnects with exponential backoff (5s → 60s)
- **Partial fills** (`executor.py`, `hedger.py`): If one leg fills but the other fails, hedger sells the filled leg to minimize loss
- **Recovery on crash** (`recovery.py`): Reconciles orphaned positions on startup by checking all platforms for open orders

**Custom exceptions:**
- `ConfigError` in `config.py` — raised on invalid env vars during import (fail-fast)
- `_RateLimitError` in `polymarket_api.py`, `kalshi_api.py` — raised on HTTP 429, triggers retry


## Cross-Cutting Concerns

**Logging:** `%`-style (not f-strings except in `executor.py`), levels INFO/DEBUG, log at scan start/end, execution gates, errors. Module-level logger: `logger = logging.getLogger(__name__)`

**Validation:**
- Env vars: `config.py:validate_config()` runs at import time, raises `ConfigError` on bad values
- Opportunities: RiskManager gates check profit, balance, depth before execution
- Prices: CLOB refinement re-checks profitability at ask prices (what you actually pay)

**Authentication:**
- Polymarket: Ethereum private key → CLOB API via `py-clob-client`
- Kalshi: RSA-PSS signed headers (key file or base64)
- Betfair: SSO login + API key
- Gemini: HMAC-SHA384 signed headers
- IBKR: TWS API via `ib_insync` socket connection
- All: Read from env vars via `config.py`

**Concurrency:**
- Scanning: ThreadPoolExecutor for parallel market fetch + parallel scan runs
- Execution: Per-market locks in continuous mode prevent double-execution on same market
- DB: Threading lock + SQLite WAL mode for concurrent reads during writes
- Price cache: Plain dict (no lock needed; Python's GIL makes dict updates atomic for single keys)

**Platform Fallback:**
- If a platform's WS is down: price cache ages out, REST API queries continue
- If REST API is slow: one-shot scan still completes, just slower
- If balance check fails: opportunity is rejected (conservative), continues scanning

---

*Architecture analysis: 2026-03-17*
