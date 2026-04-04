# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python CLI tool that scans for arbitrage opportunities across prediction markets. Supports one-shot scans, continuous mode with WebSocket feeds, and automated trade execution. Deployed to Railway via GitHub integration.

**Platforms**: Polymarket, Kalshi, Betfair, Smarkets, SX Bet, Matchbook, Gemini Predictions, IBKR ForecastEx (+ Metaculus as read-only signal source)

## Project Scope

- **What does "done" look like?** Profitable 24/7 automated trading bot on Railway — 20 strategies across 5 risk layers (pure arbitrage, near-arbitrage, market making, informed trading, capital optimization) operating across all 8 platforms. Full-stack: detection, execution, risk management, market making, monitoring, and backtesting — all production-grade and battle-tested.
- **How will you know it's working?** All three: (1) Net positive P&L in trades.db over a 7-day live trading period, (2) <5% false positive rate on detected opportunities (manually verified against platforms), (3) At least one profitable round-trip trade executed without human intervention.
- **What is explicitly out of scope?** Public-facing product — no user accounts, SaaS interface, or selling access. This is a personal trading tool.
- **Scope status:** Active

**Strategies (20 across 5 layers)**:

*Layer 1 — Pure Arbitrage (risk-free):*
- **Binary/NegRisk internal** — same-platform overround arbs on Polymarket, Kalshi, Gemini, IBKR
- **Back-all/Back-lay** — exchange-specific arbs on Betfair, Smarkets, SX Bet, Matchbook
- **Cross-platform 2-way** — mispricings between any pair of 8 platforms (28 pairs)
- **Multi-outcome cross-platform** — cheapest YES per outcome across platforms
- **Triangular** — 3-way mispricings across 3+ platforms

*Layer 2 — Near-Arbitrage (near risk-free):*
- **Resolution sniping** — buy near-certain outcomes at a discount before settlement
- **Stale price exploitation** — trade against slow-updating platforms after price moves
- **Fee promotional arbitrage** — route through lowest-fee platform paths

*Layer 3 — Market Making (low risk):*
- **Passive market making** — bid/ask spread capture on liquid markets
- **Cross-platform market making** — opposing limit orders across platforms
- **Inventory-hedged MM** — cross-platform hedging to neutralize directional exposure

*Layer 4 — Informed Trading (moderate risk):*
- **Event divergence** — multi-source consensus vs platform price signals
- **Cross-platform convergence** — directional bets on outlier platforms converging to median
- **Multi-source signal aggregation** — weighted consensus from Metaculus, Manifold, prediction polls

*Layer 5 — Capital Optimization (multiplier):*
- **Dynamic fee routing** — lowest-fee path selection for each opportunity
- **Kelly criterion sizing** — optimal position sizing by strategy risk class
- **Platform fund rebalancing** — capital allocation across platforms by opportunity flow
- **Latency optimization** — priority execution for time-sensitive opportunities
- **Backtesting-driven tuning** — historical data to optimize all thresholds
- **Spread detection** — Polymarket/Kalshi bid-ask spreads (existing, feeds into MM)


## Current Status

- **Last session**: 2026-04-04 5:01 PM
- **Worked on**: 11 commit(s): docs(06-01): complete analytics plan SUMMARY.md
- **Next recommended**: GSD Phase 2/5 — continue execution
- **Project type**: dev-only | GSD Phase 1/5
## Commands

```bash
# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt   # pytest (dev only)

# One-shot scan (all arb types)
python scanner.py

# Continuous mode
python scanner.py --continuous --interval 60

# Specific modes: binary, negrisk, cross, kalshi, cross-all, spread,
#   betfair, smarkets, sxbet, matchbook, gemini, ibkr, event, triangular,
#   multi-cross, stale, resolution, convergence, mm
python scanner.py --mode kalshi
python scanner.py --mode cross-all
python scanner.py --mode event        # Metaculus divergence signals
python scanner.py --mode triangular   # 3-way cross-platform arbs
python scanner.py --mode stale        # Stale price exploitation
python scanner.py --mode resolution   # Resolution sniping
python scanner.py --mode convergence  # Cross-platform convergence
python scanner.py --mode mm           # Market making

# Execution controls
python scanner.py --dry-run                         # detect only (default)
python scanner.py --exec-mode full-auto --max-trade 10  # live trading

# Run all tests
pytest tests/ -v

# Run a single test file or class
pytest tests/test_fees.py -v
pytest tests/test_executor.py::TestExecutor -v

# Docker build (used by CI/CD)
docker build -t polymarket-arb-scanner .
```

No linter or formatter configured. Style is enforced by convention only (see Code Style below).

## Architecture

The codebase has three layers and a thin orchestration shell:

### Orchestration (scanner.py → cli.py, continuous.py, display.py)

`scanner.py` is a **re-export facade** — it imports everything from the actual implementation modules and re-exports them so that `import scanner` continues to work for backward compatibility and test patching. The real entry point is `cli.py:main()`, which parses CLI args, initializes all platform clients, and dispatches to either `_run_oneshot()` or `continuous.py:run_continuous()`.

**Data flow in one-shot mode** (`cli.py:_run_oneshot`):
1. **Parallel fetch** — ThreadPoolExecutor fetches Polymarket markets, events, and Kalshi data simultaneously
2. **Parallel scan** — ThreadPoolExecutor runs binary, negrisk, kalshi_binary, kalshi_multi scans
3. **Sequential cross-platform** — cross/cross-all scans run after (they need data from step 1)
4. **Platform-specific scans** — spread, betfair, smarkets, sxbet, matchbook back-all/back-lay, gemini binary/multi, ibkr binary
5. **Advanced strategies** — event divergence (Metaculus signals), triangular (3-way cross-platform)
6. **Sort by capital efficiency** — `capital_efficiency_score()` ranks by ROI * depth
7. **Display + Execute** — results shown, then executor runs on each opportunity

**Continuous mode** (`continuous.py:run_continuous`) adds:
- `asyncio` event loop with graceful shutdown via SIGINT/SIGTERM
- `FeedManager` WebSocket feeds for real-time Polymarket + Kalshi prices
- `OpportunityIndex` maps (platform, ticker) → opportunities for O(1) WS-triggered execution
- Price cache with 60s TTL, shared between WS feeds and executor for revalidation
- Per-market locks prevent concurrent execution on the same market
- Crash recovery via `recovery.py:reconcile_orphaned_positions()` on startup

### Scan Layer (scans/ package)

Each scan module follows the same two-stage pattern:
1. **Mid-price scan** (fast) — uses REST API mid prices to find candidates
2. **CLOB refinement** (accurate) — re-checks candidates against actual ask prices via `_refine_*_with_clob()`

The `_refine` step can drop candidates that looked profitable at mid prices but aren't at ask prices.

Scan modules:
- `binary.py`, `negrisk.py` — Polymarket internal arbs
- `kalshi.py` — Kalshi binary + multi-outcome
- `cross.py` — 2-way cross-platform (all platform pairs)
- `spread.py` — Polymarket/Kalshi bid-ask spreads
- `betfair.py`, `smarkets.py`, `sxbet.py`, `matchbook.py` — exchange back-all/back-lay
- `gemini.py` — Gemini binary + multi-outcome (1% maker / 5% taker fees)
- `ibkr.py` — IBKR ForecastEx binary only (BUY-only, $0.00 commission)
- `multi_cross.py` — multi-outcome cross-platform (cheapest YES per outcome across Polymarket + Kalshi, fuzzy event-title matching)
- `triangular.py` — 3-way cross-platform (union-find grouping of pairwise matches)
- `stale.py` — stale price exploitation (detects slow-updating platforms). **Note:** In one-shot mode (`scanner.py` without `--continuous`), the stale scan runs but produces no results because it requires historical WebSocket price data to detect staleness. Use `--continuous` mode for real stale detection. One-shot mode logs an informational warning.
- `resolution.py` — resolution sniping (near-certain outcomes at a discount)
- `convergence.py` — cross-platform convergence (outlier price → median)
- `helpers.py` — shared utilities (token IDs, CLOB fetch, scoring)

### Execution Layer (executor.py, risk_manager.py, db.py)

`ArbitrageExecutor.execute(opp)` is the core execution path:
1. **Risk check** — RiskManager validates position limits, daily loss, balance, depth, reentry rules
2. **Gas monitor gate** — GasMonitor rejects if profit < dynamic gas+fee threshold (when enabled)
3. **Price revalidation** — re-fetches live prices; rejects if profit dropped >10% (adaptive floor)
4. **Dynamic sizing** — adjusts trade size based on depth and aggressiveness setting
5. **Order placement** — dispatches to platform-specific trader (PolymarketTrader, KalshiClient, MatchbookClient, etc.)
6. **Fill confirmation** — polls order status every 100ms for up to 2s
7. **DB logging** — records opportunity, trades, and position in SQLite (thread-safe with WAL mode)

### Platform API Clients

Each `*_api.py` wraps a platform's REST API with auth, retries (`tenacity`), and proxy support. Key auth methods:
- **Polymarket**: Ethereum private key → CLOB API (py-clob-client)
- **Kalshi**: RSA-PSS signed headers (key file or base64)
- **Betfair**: SSO login + API key
- **Smarkets**: API key session
- **SX Bet**: API key session
- **Matchbook**: Username/password session auth (0% commission on predictions)
- **Gemini Predictions**: HMAC-SHA384 signed headers (API key + secret), 1% maker / 5% taker fees (`GEMINI_FEE_RATE`), full buy+sell
- **IBKR ForecastEx**: TWS API via `ib_insync` (IB Gateway socket), BUY-only (no sell), LMT-only, $0.00 commission, 5s order rate limit
- **Metaculus**: Public REST API (optional API key), read-only signal source

### Supporting Modules

- `matcher.py` — Fuzzy title matching with `thefuzz` for cross-platform market pairing
- `fees.py` — Net profit calculators accounting for platform-specific fee structures (including `net_profit_triangular` for 3-way arbs)
- `hedger.py` — Partial fill hedger: sells filled legs when the other side fails (all trading platforms except IBKR — BUY-only)
- `gas_monitor.py` — Real-time Polygon gas price monitoring, dynamic fee thresholds replacing static MIN_NET_ROI
- `event_monitor.py` — Metaculus divergence signal detection (fuzzy-matches platform markets to Metaculus questions)
- `metaculus_api.py` — Read-only Metaculus client for community probability forecasts
- `manifold_api.py` — Read-only Manifold Markets client for probability estimates
- `signal_aggregator.py` — Multi-source probability aggregation (Metaculus, Manifold, weighted consensus)
- `price_tracker.py` — Rolling price tracker with staleness detection across platforms
- `market_maker.py` — Market making engine (QuoteEngine, InventoryTracker, QuoteManager)
- `position_sizer.py` — Kelly criterion + strategy-aware position sizing
- `notifier.py` — Async webhook alerts (auto-detects Slack/Discord/generic format)
- `dashboard.py` — Lightweight HTTP server exposing `/status` JSON endpoint
- `recovery.py` — Reconciles orphaned positions/trades after crashes (supports all 8 trading platforms)
- `config.py` — All constants backed by env vars with sensible defaults; `validate_config()` runs at import time
- `snapshot.py` — Historical price snapshot recorder (SQLite, thread-safe) for backtesting
- `backtest.py` — Replay engine that simulates execution over recorded snapshots (standalone CLI: `python backtest.py`)
- `alerting.py` — Structured rate-limited alerts with severity levels, integrates with `notifier.py`
- `metrics.py` — Stdlib-only metrics (counters, gauges, histograms) with Prometheus text exposition
- `dashboard_ui.py` — Dashboard HTML template served by `dashboard.py` at GET `/`
- `run_dashboard.py` — Standalone dashboard launcher for local testing

## Key Patterns

- **scanner.py is a facade**: Never add logic here. It only re-exports from `scans/`, `cli.py`, `continuous.py`, `display.py` for backward compatibility. Tests patch `scanner.<name>` which hits these re-exports.
- **Two-stage detection**: Mid prices (fast) → CLOB ask prices (accurate). All scan modules follow this.
- **Token ID resolution**: CLOB token IDs are extracted during scanning (`_extract_token_ids`) and attached to opportunity dicts as `_token_ids` for use during execution.
- **Opportunity dicts**: Opportunities flow through the system as plain dicts with standardized keys: `type`, `market`, `prices`, `total_cost`, `net_profit`, `net_roi`, `_token_ids`, `_clob_depth`, `_market_key`, etc. Internal keys prefixed with `_`.
- **Thread safety**: `TradeDB` uses a threading lock on all operations + SQLite WAL mode. Price cache is a plain dict updated from WS threads. Per-market locks in continuous mode prevent double execution.
- **Config precedence**: CLI args > env vars > defaults in `config.py`.
- **Parallel everything**: Data fetching, scanning, and execution all use `ThreadPoolExecutor`.
- **`_build_legs` dispatcher**: `executor.py:_build_legs()` converts an opportunity dict into execution legs by switching on `opp["type"]`. When adding a new opportunity type, add the corresponding branch here and a matching `_revalidate` case.

### Adding a new opportunity type

1. Create the scan in `scans/<name>.py` following the two-stage pattern.
2. Add the fee function in `fees.py`.
3. Add a branch in `executor.py:_build_legs()` and a matching `_revalidate` case.
4. Wire it into `cli.py:_run_oneshot()` and `continuous.py` if applicable.
5. Add the mode string to `cli.py` argparse choices.

### Adding a new cross-platform pair

Add entries to `_CROSS_FEE_FUNCS` in `scans/cross.py` using `functools.partial(net_profit_cross_generic, buy_fee, sell_fee)`. All 28 pairs of the 8 trading platforms are already covered.

## Code Style

- **Python 3.10+**. Use modern union syntax: `X | None`, `list[float]`, `tuple[bool, str]`. Never use `Optional`, `List`, `Dict`, `Tuple` from `typing`.
- Double quotes for strings. ~120 char soft line limit.
- Logging with `%`-style: `logger.info("Found %d opps in %s", count, market)`. (`executor.py` is the sole exception using f-strings.)
- Section separators: `# ---------------------------------------------------------------------------` (75 dashes) between logical sections.
- Three custom exceptions: `ConfigError(ValueError)` in `config.py`, `_RateLimitError(Exception)` in `kalshi_api.py` and `polymarket_api.py`.
- Relative imports within `scans/` package (`from .helpers import ...`), absolute elsewhere.

## Testing

Tests use `pytest` with `unittest.mock`. All tests are methods inside classes (no module-level test functions). No `conftest.py` exists; shared setup uses per-file `autouse` fixtures.

External SDKs are mocked via `sys.modules` stubs before importing the module under test (see `test_executor.py` for the pattern). Tests path-insert the parent directory for imports (no package install needed).

**`autouse` fixture caveat**: Fixtures that clean `sys.modules` must only remove the specific scan module under test (e.g. `scans.gemini`), never `scans.helpers` or `scans.__init__` — this prevents cross-test pollution.

Run a specific test: `pytest tests/test_fees.py::TestPolymarketFee::test_zero_when_sell_equals_buy -v`

## CI / CD

- `.github/workflows/test.yml` runs `pytest` on every PR to `master` (Python 3.12, installs both `requirements.txt` and `requirements-dev.txt`).
- CI fails on any test failures or errors (zero tolerance).

## Deployment

- **Railway** auto-deploys on push to `master` via GitHub integration. Dockerfile-based build (`python:3.12-slim`). Health check: `/healthz` on port 8080.
- **Docker**: Runs `scanner.py --continuous` as entrypoint.
- **Data persistence**: `DATA_DIR` env var for `trades.db`

## Environment Variables

All env vars are defined in `config.py` with defaults. Key groups:
- Platform credentials: `POLYMARKET_PRIVATE_KEY`, `KALSHI_API_KEY_ID`/`KALSHI_PRIVATE_KEY_PATH` (or `_BASE64`), `BETFAIR_*`, `SMARKETS_API_KEY`, `SXBET_API_KEY`, `MATCHBOOK_USERNAME`/`MATCHBOOK_PASSWORD`, `GEMINI_API_KEY`/`GEMINI_API_SECRET`, `IBKR_HOST`/`IBKR_PORT`/`IBKR_CLIENT_ID` (IB Gateway), `METACULUS_API_KEY` (optional)
- Execution: `DRY_RUN` (default: true), `EXECUTION_MODE`, `MAX_TRADE_SIZE`
- Risk: `DAILY_LOSS_LIMIT`, `MAX_OPEN_POSITIONS`, `MIN_LIQUIDITY`, `MIN_NET_ROI`
- Dynamic fees: `DYNAMIC_FEE_ENABLED`, `POLYGON_RPC_URL`, `GAS_PRICE_CACHE_TTL`
- Event monitor: `EVENT_MONITOR_ENABLED`, `EVENT_DIVERGENCE_THRESHOLD`
- Tuning: `RESCAN_INTERVAL`, `WS_TRIGGER_THRESHOLD`, `WS_SUBSCRIPTION_LIMIT`, `FUZZY_MATCH_THRESHOLD`
- Infra: `WEBHOOK_URL`, `DASHBOARD_PORT`, `DATA_DIR`, `LOG_LEVEL`, `LOG_FILE`
- Proxies: `POLYMARKET_PROXY_URL`, `KALSHI_PROXY_URL`

### Railway Production Configuration

The following env vars should be set in Railway for production deployment (Railway Dashboard -> Project -> Service -> Variables):

**Feature Flags (all default to false in config.py):**
- `MM_ENABLED=true` — Enable market making engine
- `SNAPSHOT_ENABLED=true` — Enable price snapshot recording for backtesting
- `DYNAMIC_FEE_ENABLED=true` — Enable real-time Polygon gas monitoring
- `EVENT_MONITOR_ENABLED=true` — Enable Metaculus/Manifold signal aggregation

**Market Making Tuning:**
- `MM_MIN_SPREAD=0.02` — 2% minimum spread width
- `MM_MAX_INVENTORY=500.0` — $500 per market inventory cap

**Dynamic Fees:**
- `POLYGON_RPC_URL=https://polygon-rpc.com` — Polygon RPC for gas monitoring (or use Alchemy/Infura for reliability)

**Platform Credentials (all 8 trading platforms):**
Already documented above. Ensure ALL platform credentials are set for full cross-platform coverage: `POLYMARKET_PRIVATE_KEY`, `KALSHI_API_KEY_ID`/`KALSHI_PRIVATE_KEY_PATH`, `BETFAIR_APP_KEY`/`BETFAIR_USERNAME`/`BETFAIR_PASSWORD`, `SMARKETS_API_KEY`, `SXBET_API_KEY`, `MATCHBOOK_USERNAME`/`MATCHBOOK_PASSWORD`, `GEMINI_API_KEY`/`GEMINI_API_SECRET`, `IBKR_HOST`/`IBKR_PORT`/`IBKR_CLIENT_ID`.

Note: `POLYMARKET_PRIVATE_KEY` and `KALSHI_API_KEY_ID`/`KALSHI_PRIVATE_KEY_PATH` should already be configured from initial deployment. The IBKR connection requires IB Gateway running and reachable from Railway — requires a persistent IB Gateway host (not a local machine).

## Agent Team Notes

When splitting work across teammates:
- **API layer** (`*_api.py`, `ws_feeds.py`) — platform integration, auth, data fetching (9 platform clients)
- **Analysis layer** (`scans/`, `matcher.py`, `fees.py`, `config.py`, `event_monitor.py`, `gas_monitor.py`) — detection logic, matching, profit calculation, signal processing
- **Execution layer** (`executor.py`, `risk_manager.py`, `db.py`) — trade execution, risk management, persistence
- **Orchestration** (`cli.py`, `continuous.py`, `display.py`, `scanner.py`) — entry points, loops, output

Avoid two teammates editing the same module. `cli.py` and `continuous.py` are large — coordinate carefully.
