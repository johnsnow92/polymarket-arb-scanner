# Phase 6: Monitor & Harden - Context

**Gathered:** 2026-04-01
**Status:** Ready for planning

<domain>
## Phase Boundary

Full observability and reliability for the trading bot. Every strategy gets observable P&L attribution, disconnects are detected and tagged, credential health is proactively monitored, and the system is reliable enough to leave running unattended for days.

Requirements: MON-01, MON-02, MON-03, HARD-01, HARD-02, HARD-03.

</domain>

<decisions>
## Implementation Decisions

### Analytics Engine
- DuckDB for per-strategy P&L analytics over trades.db — embedded OLAP, no server needed
- Standalone CLI script (`scripts/analytics.py`) — run on-demand or scheduled, no bot impact
- Rolling 7-day window for Sharpe ratio calculation (matches project scope "7-day live trading period")
- Strategies with zero trades show "N/A" with opportunity count (shows detection without execution)

### Dashboard Strategy Leaderboard
- Extend existing dashboard (`dashboard.py` + `dashboard_ui.py`) — already serves HTML at `/` and JSON at `/status`
- Leaderboard data refreshes every scan cycle (~60s) — piggybacks on existing scan loop
- Metrics per strategy: win rate, trade count, net P&L, average ROI, max drawdown
- Drawdown = peak-to-trough on cumulative P&L per strategy (standard calculation)

### Alerting & Loss Detection
- Use existing webhook delivery (`notifier.py` → auto-detects Slack/Discord format)
- Loss streak threshold: 3 consecutive losses per strategy triggers alert
- Zero-opportunity alert window: 30 minutes with zero opps for any strategy
- Alert rate limiting: 5 min cooldown per alert type (existing `AlertManager` default)

### WS & Credential Hardening
- WS disconnect detection via heartbeat timeout — no message in 30s = mark stale
- Stale price propagation: add `_stale: true` flag in price_cache dict, executor skips stale-tagged markets
- Credential health check: lightweight auth probe per platform every 30 minutes (cheap endpoint like `/balances`)
- Credential expiry alerting: 24h pre-expiry for time-limited tokens (Betfair SSO, Smarkets sessions); API keys (Kalshi, Gemini) don't expire

### Claude's Discretion
- DuckDB query structure and table design
- Dashboard HTML/JS layout for leaderboard section
- Specific cheap auth endpoints per platform for health checks
- Hedger validation test structure (HARD-02)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `dashboard.py` — HTTP server with `/status`, `/metrics`, `/healthz`, `/alerts` endpoints, auth middleware
- `dashboard_ui.py` — HTML template served at GET `/`, already has opportunity display
- `db.py:TradeDB` — SQLite with WAL mode, has `get_daily_pnl`, `get_open_positions`, `log_trade`, `log_opportunity`
- `alerting.py:AlertManager` — rate-limited alerts with `check_loss_streak`, `check_zero_opp_period`, `check_daily_loss`
- `metrics.py:MetricsCollector` — Prometheus text exposition with counters, gauges, histograms
- `notifier.py` — async webhook delivery, auto-detects Slack/Discord format
- `ws_feeds.py` — WebSocket feeds with existing reconnect logic
- `price_tracker.py` — rolling price tracker with staleness detection
- `backtest.py` — has STRATEGY_LAYERS and per-strategy analytics logic

### Established Patterns
- Dashboard uses `BaseHTTPRequestHandler` with auth middleware (`_check_auth`)
- `_DashboardState` singleton holds scan-level metrics updated by `continuous.py`
- AlertManager uses enum types (`AlertType`, `Severity`) with rate-limited delivery
- All DB operations thread-safe via `threading.Lock` + WAL mode
- Config values backed by env vars with `_env_float`/`_env_int`/`_env_bool` pattern

### Integration Points
- `continuous.py` updates `_DashboardState` after each scan — leaderboard data hooks here
- `executor.py` logs trades to `TradeDB` — strategy attribution needs `opp["type"]` and `opp["_layer"]`
- `ws_feeds.py` reconnect callbacks — staleness tagging hooks here
- `alerting.py` already called from `executor.py` and `continuous.py` — extend with new alert types

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches. Existing infrastructure (dashboard, alerting, metrics, DB) is solid — phase is about extending, not rebuilding.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>
