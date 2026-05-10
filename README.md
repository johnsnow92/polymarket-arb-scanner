# arbgrid

Python CLI that scans for arbitrage and trading opportunities across prediction markets, executes trades automatically, and runs 24/7 on Railway.

> Renamed from `polymarket-arb-scanner` on 2026-05-09. The new name reflects the actual scope — a grid of platforms × layers × strategies, not a Polymarket-only scanner. Local clones may keep the old directory name without any functional impact.

## What It Does

`arbgrid` watches 8 trading venues for mispricings, low-risk edges, and market-making opportunities, then executes against them subject to risk limits. It supports:

- **One-shot scans** — detect opportunities across all enabled strategies and exit.
- **Continuous mode** — long-running loop with WebSocket feeds, opportunity index, and per-market locking.
- **Automated execution** — dry-run by default; switch to `full-auto` once configured.
- **Backtesting** — replay engine over recorded price snapshots.

### Platforms

| Role | Platforms |
|---|---|
| Trading (execute) | Polymarket, Kalshi, Betfair, Smarkets, SX Bet, Matchbook, Gemini Predictions, IBKR ForecastEx |
| Signal-only (read) | Metaculus, Manifold |

### Strategy Coverage

29 strategies across 5 risk layers. Canonical taxonomy and status table live in [`docs/strategy-framework-v2.md`](docs/strategy-framework-v2.md). Summary:

| Layer | Theme | Strategies |
|---|---|---|
| 1 — Pure Arbitrage | Risk-free; same-platform overrounds, cross-platform 2-way, multi-outcome, triangular, crossed-book | 6 |
| 2 — Near-Arbitrage | Resolution sniping, stale-price exploitation, fee promotional arbitrage | 3 |
| 3 — Market Making | Passive MM, cross-platform MM, inventory-hedged MM, liquidity rewards farming | 5 |
| 4 — Informed Trading | Event divergence, cross-platform convergence, multi-source signal aggregation | 9 |
| 5 — Capital Optimization | Kelly sizing, fee routing, rebalancing, latency, backtesting-driven tuning | 6 |

As of the May 2026 milestone: **23 BUILT**, **5 PARTIAL**, **1 STUB**. See the framework doc for what's pending.

## Quick Start

```bash
# Install
pip install -r requirements.txt
pip install -r requirements-dev.txt   # pytest, dev tools

# One-shot scan (all strategies, dry-run)
python scanner.py

# Continuous mode
python scanner.py --continuous --interval 60

# Single strategy
python scanner.py --mode kalshi
python scanner.py --mode cross-all
python scanner.py --mode mm

# Live trading (requires platform credentials configured)
python scanner.py --exec-mode full-auto --max-trade 10
```

Available `--mode` values: `binary`, `negrisk`, `cross`, `kalshi`, `cross-all`, `spread`, `betfair`, `smarkets`, `sxbet`, `matchbook`, `gemini`, `ibkr`, `event`, `triangular`, `multi-cross`, `stale`, `resolution`, `convergence`, `mm`, `fee-promo`, `cross-mm`.

## Architecture

Three layers plus a thin orchestration shell. `scanner.py` is a **facade** — it re-exports from the real implementation modules so `import scanner` keeps working for tests and backward compatibility. The real entry point is `cli.py:main()`.

```
CLI (scanner.py facade) → cli.py → _run_oneshot()  or  continuous.py:run_continuous()
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
          API Layer      Scan Layer      Execution Layer
         (*_api.py)      (scans/)       (executor.py,
          ws_feeds.py                    risk_manager.py,
                                         db.py)
```

- **API layer** (`*_api.py`, `ws_feeds.py`) — platform clients with auth, retries, proxies.
- **Scan layer** (`scans/`) — two-stage detection: mid-price scan (fast) → CLOB refinement (accurate).
- **Execution layer** (`executor.py`, `risk_manager.py`, `db.py`) — risk gates, dynamic sizing, fill confirmation, SQLite persistence.
- **Orchestration** (`cli.py`, `continuous.py`, `display.py`) — argument parsing, parallel fetch/scan via `ThreadPoolExecutor`, WebSocket-driven re-execution.

Full module map: [`CODEBASE-INVENTORY.md`](CODEBASE-INVENTORY.md). Detailed conventions and patterns: [`CLAUDE.md`](CLAUDE.md).

## Configuration

All settings are env vars with defaults defined in `config.py`. `validate_config()` runs at import time and refuses to start if anything required is missing.

Key groups:

- **Platform credentials** — `POLYMARKET_PRIVATE_KEY`, `KALSHI_API_KEY_ID` + `KALSHI_PRIVATE_KEY_PATH`, `BETFAIR_*`, `SMARKETS_API_KEY`, `SXBET_API_KEY`, `MATCHBOOK_USERNAME`/`MATCHBOOK_PASSWORD`, `GEMINI_API_KEY`/`GEMINI_API_SECRET`, `IBKR_HOST`/`IBKR_PORT`/`IBKR_CLIENT_ID`, optional `METACULUS_API_KEY`.
- **Execution** — `DRY_RUN` (default `true`), `EXECUTION_MODE`, `MAX_TRADE_SIZE`.
- **Risk** — `DAILY_LOSS_LIMIT`, `MAX_OPEN_POSITIONS`, `MIN_LIQUIDITY`, `MIN_NET_ROI`.
- **Feature flags** (default `false`) — `MM_ENABLED`, `SNAPSHOT_ENABLED`, `DYNAMIC_FEE_ENABLED`, `EVENT_MONITOR_ENABLED`, `MM_AUTO_HEDGE_ENABLED`, `FEE_PROMO_ENABLED`, `CROSS_MM_ENABLED`, `AUTO_REBALANCE_ENABLED`.
- **Infra** — `WEBHOOK_URL`, `DASHBOARD_PORT`, `DASHBOARD_HOST`, `DASHBOARD_PASS`, `DATA_DIR`, `LOG_LEVEL`, `LOG_FILE`.

Production deploys should set the feature flags explicitly. Full env-var reference is in [`CLAUDE.md`](CLAUDE.md#environment-variables).

## Testing

```bash
pytest tests/ -v                                        # full suite
pytest tests/test_fees.py -v                            # one file
pytest tests/test_executor.py::TestExecutor -v          # one class
```

Tests use `pytest` + `unittest.mock`. External SDKs are mocked via `sys.modules` stubs. No `conftest.py` — per-file `autouse` fixtures provide shared setup.

CI runs `pytest` on every PR to `master` (Python 3.12) and fails on any test failure.

## Deployment

- **Railway** auto-deploys on push to `master` via GitHub integration. Dockerfile-based build (`python:3.12-slim`).
- Entrypoint: `python scanner.py --continuous`.
- Health check: `/healthz` on port 8080.
- Persistent state (`trades.db`) lives under `DATA_DIR`.

IBKR connectivity requires a reachable IB Gateway socket — not viable from Railway without a persistent gateway host.

## Project Status

- 9 of 9 planning phases complete.
- Active development against the v2 strategy framework remediation roadmap.
- Live trading: dry-run calibration; gated behind feature flags and `DRY_RUN`.
- This is a personal trading tool. **Out of scope:** public-facing product, SaaS, user accounts, selling access.

## Further Reading

| Document | What's in it |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Canonical project guide — overview, architecture, env vars, code style, patterns, agent notes. |
| [`docs/strategy-framework-v2.md`](docs/strategy-framework-v2.md) | Authoritative 29-strategy / 5-layer reconciliation with per-strategy status. |
| [`CODEBASE-INVENTORY.md`](CODEBASE-INVENTORY.md) | File-by-file map of modules, functions, classes, and cross-references. |
| [`AGENTS.md`](AGENTS.md) | Notes for AI agents working on the codebase. |
