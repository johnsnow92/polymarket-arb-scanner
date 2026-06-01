# Architecture

> **Owner:** Jonathon Tamm · **Review cadence:** quarterly, or on any change to the layer boundaries / data flow.
> System design reference. CLAUDE.md holds the working dev-guidance; this is the canonical design doc.

## Shape

Three layers plus a thin orchestration shell. `scanner.py` is a **facade** — it re-exports from the real implementation modules so `import scanner` keeps working for tests and back-compat. The real entry point is `cli.py:main()`.

```
CLI (scanner.py facade) → cli.py:main()
                              │  parse args, init platform clients
                              ├── _run_oneshot()         (one-shot)
                              └── continuous.py:run_continuous()  (24/7)
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
          API Layer      Scan Layer      Execution Layer
        (*_api.py,       (scans/)        (executor.py,
         ws_feeds.py)                     risk_manager.py, db.py)
```

## Layers

**API layer** (`*_api.py`, `ws_feeds.py`) — one client per platform (9 trading/signal clients). Each wraps a REST/socket API with auth, `tenacity` retries, and proxy support. See `PLATFORM-MATRIX.md` for per-platform auth.

**Scan layer** (`scans/`) — detection. Every scan follows the **two-stage pattern**: (1) mid-price scan (fast, REST mid prices) finds candidates; (2) CLOB refinement (`_refine_*_with_clob`) re-checks against real ask prices and can drop candidates that only looked profitable at mid. Supporting analysis: `matcher.py` (fuzzy title matching), `fees.py` (per-platform net-profit), `signal_aggregator.py`, `gas_monitor.py`.

**Execution layer** (`executor.py`, `risk_manager.py`, `db.py`) — `ArbitrageExecutor.execute(opp)`: risk check → gas-monitor gate → price revalidation (reject if profit dropped >10%) → dynamic sizing → platform-specific order placement → fill confirmation (poll 100ms up to 2s) → SQLite logging (WAL, thread-safe). `_build_legs()` dispatches on `opp["type"]`; each new opp type needs a matching `_revalidate` case.

**Orchestration** (`cli.py`, `continuous.py`, `display.py`) — arg parsing, parallel fetch/scan via `ThreadPoolExecutor`, and the async continuous loop.

## Data flow — one-shot (`cli.py:_run_oneshot`)
1. Parallel fetch (Polymarket markets/events + Kalshi) via `ThreadPoolExecutor`.
2. Parallel scan (binary, negrisk, kalshi_binary, kalshi_multi).
3. Sequential cross-platform scans (need step-1 data).
4. Platform-specific scans (spread, betfair, smarkets, sxbet, matchbook, gemini, ibkr).
5. Advanced (event divergence, triangular, …).
6. Rank by `capital_efficiency_score()` (ROI × depth).
7. Display + execute.

## Data flow — continuous (`continuous.py:run_continuous`)
- `asyncio` loop with graceful SIGINT/SIGTERM shutdown.
- `FeedManager` WebSocket feeds for real-time Polymarket + Kalshi prices.
- `OpportunityIndex` maps `(platform, ticker)` → opportunities for O(1) WS-triggered execution.
- Price cache (60s TTL) shared between WS feeds and executor for revalidation.
- Per-market locks prevent concurrent execution on the same market.
- `_execution_priority()` + `asyncio.PriorityQueue` for latency-sensitive ordering.
- Crash recovery: `recovery.py:reconcile_orphaned_positions()` on startup.

## Key invariants
- **Facade discipline:** never add logic to `scanner.py`.
- **Opportunity dicts** flow through as plain dicts (see `TASK_CONTRACT.md` for the schema). Internal keys are `_`-prefixed.
- **Thread safety:** `TradeDB` uses a lock + WAL; per-market locks in continuous mode.
- **Config precedence:** CLI args > env vars > `config.py` defaults; `validate_config()` runs at import.

## Deployment topology
Railway (Docker, `python:3.12-slim`) runs `scanner.py --continuous`; health check `/healthz` on 8080; `trades.db`/`snapshots.db` under `DATA_DIR`. IBKR requires a reachable IB Gateway socket (not viable from Railway without a persistent gateway host). See `RUNBOOK.md`.
