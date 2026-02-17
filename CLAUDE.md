# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python CLI tool that scans for arbitrage opportunities across prediction markets (Polymarket, Kalshi, PredictIt, Betfair, Manifold Markets). Supports one-shot scans, continuous mode with WebSocket feeds, and automated trade execution. Deployed to AWS ECS Fargate via GitHub Actions CI/CD.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# One-shot scan (all arb types)
python scanner.py

# Continuous mode
python scanner.py --continuous --interval 60

# Specific modes: binary, negrisk, cross, kalshi, cross-all
python scanner.py --mode kalshi
python scanner.py --mode cross-all

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

## Architecture

The codebase has three layers and a thin orchestration shell:

### Orchestration (scanner.py → cli.py, continuous.py, display.py)

`scanner.py` is a **re-export facade** — it imports everything from the actual implementation modules and re-exports them so that `import scanner` continues to work for backward compatibility and test patching. The real entry point is `cli.py:main()`, which parses CLI args, initializes all platform clients, and dispatches to either `_run_oneshot()` or `continuous.py:run_continuous()`.

**Data flow in one-shot mode** (`cli.py:_run_oneshot`):
1. **Parallel fetch** — ThreadPoolExecutor fetches Polymarket markets, events, and Kalshi data simultaneously
2. **Parallel scan** — ThreadPoolExecutor runs binary, negrisk, kalshi_binary, kalshi_multi scans
3. **Sequential cross-platform** — cross/cross-all scans run after (they need data from step 1)
4. **Sort by capital efficiency** — `capital_efficiency_score()` ranks by ROI * depth
5. **Display + Execute** — results shown, then executor runs on each opportunity

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

The `_refine` step can drop candidates that looked profitable at mid prices but aren't at ask prices. Scan modules: `binary.py`, `negrisk.py`, `cross.py`, `kalshi.py`, `helpers.py` (shared utilities).

### Execution Layer (executor.py, risk_manager.py, db.py)

`ArbitrageExecutor.execute(opp)` is the core execution path:
1. **Risk check** — RiskManager validates position limits, daily loss, balance, depth, reentry rules
2. **Price revalidation** — re-fetches live prices; rejects if profit dropped >10% (adaptive floor)
3. **Dynamic sizing** — adjusts trade size based on depth and aggressiveness setting
4. **Order placement** — dispatches to platform-specific trader (PolymarketTrader, KalshiClient, etc.)
5. **Fill confirmation** — polls order status every 100ms for up to 2s
6. **DB logging** — records opportunity, trades, and position in SQLite (thread-safe with WAL mode)

### Platform API Clients

Each `*_api.py` wraps a platform's REST API with auth, retries (`tenacity`), and proxy support. Key auth methods:
- **Polymarket**: Ethereum private key → CLOB API (py-clob-client)
- **Kalshi**: RSA-PSS signed headers (key file or base64)
- **PredictIt**: Email/password session
- **Betfair**: SSO login + API key
- **Manifold**: API key header

### Supporting Modules

- `matcher.py` — Fuzzy title matching with `thefuzz` for cross-platform market pairing
- `fees.py` — Net profit calculators accounting for platform-specific fee structures
- `notifier.py` — Async webhook alerts (auto-detects Slack/Discord/generic format)
- `dashboard.py` — Lightweight HTTP server exposing `/status` JSON endpoint
- `recovery.py` — Reconciles orphaned positions/trades after crashes
- `config.py` — All constants backed by env vars with sensible defaults

## Key Patterns

- **scanner.py is a facade**: Never add logic here. It only re-exports from `scans/`, `cli.py`, `continuous.py`, `display.py` for backward compatibility. Tests patch `scanner.<name>` which hits these re-exports.
- **Two-stage detection**: Mid prices (fast) → CLOB ask prices (accurate). All scan modules follow this.
- **Token ID resolution**: CLOB token IDs are extracted during scanning (`_extract_token_ids`) and attached to opportunity dicts as `_token_ids` for use during execution.
- **Opportunity dicts**: Opportunities flow through the system as plain dicts with standardized keys: `type`, `market`, `prices`, `total_cost`, `net_profit`, `net_roi`, `_token_ids`, `_clob_depth`, `_market_key`, etc. Internal keys prefixed with `_`.
- **Thread safety**: `TradeDB` uses a threading lock on all operations + SQLite WAL mode. Price cache is a plain dict updated from WS threads. Per-market locks in continuous mode prevent double execution.
- **Config precedence**: CLI args > env vars > defaults in `config.py`.
- **Parallel everything**: Data fetching, scanning, and execution all use `ThreadPoolExecutor`.

## Testing

Tests use `pytest` with `unittest.mock`. The test for `executor.py` mocks external API modules in `sys.modules` before import since platform SDKs may not be installed in the test environment. Tests path-insert the parent directory for imports (no package installation needed).

Run a specific test: `pytest tests/test_fees.py::TestPolymarketFee::test_zero_when_sell_equals_buy -v`

## Deployment

- **CI/CD**: Push to `master` triggers `.github/workflows/deploy.yml` — builds Docker image, pushes to ECR, deploys to ECS Fargate
- **Manual deploy**: `bash infra/deploy.sh` (requires AWS CLI + Docker)
- **Docker**: Runs `scanner.py --continuous` as entrypoint. Health check hits dashboard at `:8080/status`
- **Data persistence**: `DATA_DIR` env var points to EFS mount for `trades.db`

## Environment Variables

All env vars are defined in `config.py` with defaults. Key groups:
- Platform credentials: `POLYMARKET_PRIVATE_KEY`, `KALSHI_API_KEY_ID`/`KALSHI_PRIVATE_KEY_PATH` (or `_BASE64`), `PREDICTIT_EMAIL`/`PASSWORD`, `BETFAIR_*`, `MANIFOLD_API_KEY`
- Execution: `DRY_RUN` (default: true), `EXECUTION_MODE`, `MAX_TRADE_SIZE`
- Risk: `DAILY_LOSS_LIMIT`, `MAX_OPEN_POSITIONS`, `MIN_LIQUIDITY`, `MIN_NET_ROI`
- Tuning: `RESCAN_INTERVAL`, `WS_TRIGGER_THRESHOLD`, `WS_SUBSCRIPTION_LIMIT`, `FUZZY_MATCH_THRESHOLD`
- Infra: `WEBHOOK_URL`, `DASHBOARD_PORT`, `DATA_DIR`, `LOG_LEVEL`, `LOG_FILE`
- Proxies: `POLYMARKET_PROXY_URL`, `KALSHI_PROXY_URL`

## Agent Team Notes

When splitting work across teammates:
- **API layer** (`*_api.py`, `ws_feeds.py`) — platform integration, auth, data fetching
- **Analysis layer** (`scans/`, `matcher.py`, `fees.py`, `config.py`) — detection logic, matching, profit calculation
- **Execution layer** (`executor.py`, `risk_manager.py`, `db.py`) — trade execution, risk management, persistence
- **Orchestration** (`cli.py`, `continuous.py`, `display.py`, `scanner.py`) — entry points, loops, output

Avoid two teammates editing the same module. `cli.py` and `continuous.py` are large — coordinate carefully.
