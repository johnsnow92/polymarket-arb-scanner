# Phase 7: Liquidity Rewards - Context

**Gathered:** 2026-04-04
**Status:** Ready for planning

<domain>
## Phase Boundary

Bot earns exchange liquidity rewards by resting limit orders on Polymarket and Kalshi. New `rewards` scan strategy type that places qualifying resting orders, tracks reward scores per platform, and optimizes quote placement for maximum USDC reward yield.

Requirements: EXEC-05, EXEC-06, STRAT-03.

</domain>

<decisions>
## Implementation Decisions

### Reward Program Integration
- Poll Polymarket CLOB reward API endpoint every scan cycle (~60s) to track reward score — same cadence as existing scans
- Log Kalshi qualifying order metrics locally (time resting, size, spread) since Kalshi has no public reward API
- New scan module `scans/rewards.py` following existing scan pattern, registered as `--mode rewards`
- Extend existing `market_maker.py` QuoteManager for reward-aware quoting — reuse InventoryTracker, add reward scoring logic

### Quote Placement Strategy
- Target high-volume qualifying markets only — filter by minimum daily volume and reward eligibility (platform-specific criteria)
- Tight spreads near mid-price for max reward score, but always outside existing arb detection thresholds to avoid self-trading
- Filled reward orders get hedged on the opposite platform via existing hedger — same as market making fills
- Quote refresh every 10s (reuse MM_REFRESH_INTERVAL) — frequent enough for reward eligibility, not so fast it burns rate limits

### Risk & Capital Allocation
- Separate capital budget from trading — new `REWARDS_MAX_EXPOSURE` config (default $200), doesn't count against `MAX_OPEN_POSITIONS`
- Config-driven eligibility rules (min size, max spread, min resting time) — hot-updatable via Railway env vars without deploy
- Filled positions hedged or unwound via existing hedger infrastructure

### Observability
- New dashboard section showing reward score, qualifying order count, and estimated reward yield per platform
- Rewards strategy gets its own row in the Phase 6 strategy leaderboard — track both trading P&L from fills and reward income separately
- Reward metrics exported via existing Prometheus `/metrics` endpoint

### Claude's Discretion
- Specific Polymarket reward API endpoint and response parsing
- Kalshi liquidity incentive program qualifying criteria and metrics
- Market selection heuristics for reward-eligible markets
- Reward score calculation and optimization algorithm
- Dashboard HTML/JS layout for rewards section

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `market_maker.py` — QuoteEngine, InventoryTracker, QuoteManager already handle limit order lifecycle
- `polymarket_api.py:place_order()` — existing limit order placement on Polymarket CLOB
- `kalshi_api.py:place_order()` — existing limit order placement on Kalshi
- `hedger.py` — partial fill hedging across all trading platforms
- `dashboard.py` + `dashboard_ui.py` — HTTP dashboard with `/status` JSON and HTML at `/`
- `metrics.py:MetricsCollector` — Prometheus counters/gauges/histograms
- `config.py` — MM_ENABLED, MM_MIN_SPREAD, MM_QUOTE_SIZE, MM_MAX_INVENTORY, MM_REFRESH_INTERVAL already defined

### Established Patterns
- Scan modules follow two-stage pattern (mid-price scan → CLOB refinement)
- New strategy types added via: scan module → fee function → `_build_legs` branch → CLI mode → continuous mode
- Config vars use `_env_float`/`_env_bool` with sensible defaults and Railway env var overrides
- Thread-safe position tracking via locks in InventoryTracker

### Integration Points
- `cli.py:_run_oneshot()` — add rewards scan call
- `continuous.py:run_continuous()` — add rewards to continuous loop
- `executor.py:_build_legs()` — add rewards opportunity type
- `config.py` — add REWARDS_* config vars
- `dashboard_ui.py` — add rewards metrics section

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches. User deferred all decisions to Claude's discretion.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>
