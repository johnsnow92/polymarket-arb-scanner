# Phase 9: Structural Alpha Strategies - Context

**Gathered:** 2026-04-05
**Status:** Ready for planning

<domain>
## Phase Boundary

Two high-complexity structural alpha strategies live in production with P&L attribution: combinatorial logical arb (detects semantic inconsistencies across related markets) and whale copy trading (mirrors profitable Polymarket wallets on-chain).

Requirements: STRAT-04, STRAT-05.

</domain>

<decisions>
## Implementation Decisions

### Combinatorial Logical Arb (STRAT-04)
- NLP-free approach: manual semantic rules mapping related markets (e.g., "Bitcoin >$100k" implies "Bitcoin >$90k")
- Config-driven rule sets in JSON format: `{"if_yes": "market_A_id", "then_yes": "market_B_id", "relationship": "implies"}`
- Loaded from LOGICAL_ARB_RULES env var (JSON string) or `logical_arb_rules.json` file
- Signal threshold: price of implied market < price of implying market by >5% = opportunity
- Execution: buy the underpriced implied outcome, Layer 4 revalidation (10% floor), max $20 per position
- New scan module: `scans/logical_arb.py`

### Whale Copy Trading (STRAT-05)
- Polygonscan API for Polymarket CLOB contract events — monitor specific wallet addresses for large trades
- Manual list of profitable wallets in config (WHALE_WALLETS env var, comma-separated addresses)
- Latency budget: <30s from on-chain trade detection to mirror order placement
- Max $15 per mirror trade, Layer 4 revalidation (10% floor), 5 concurrent whale-copy positions max
- New scan module: `scans/whale_copy.py`, new API client: `polygonscan_api.py`

### Dashboard Integration
- Both strategies appear in Phase 6 monitoring dashboard with their own P&L attribution rows
- Extend dashboard_ui.py with 2 new leaderboard rows
- Extend dashboard.py /status endpoint with strategy metrics

### Shared Infrastructure
- Each strategy gets: scan module, fee function, _build_legs branch, _revalidate case, CLI mode, continuous mode entry
- Feature flags: LOGICAL_ARB_ENABLED, WHALE_COPY_ENABLED (default false)
- All config vars use existing _env_* helpers with sensible defaults

### Claude's Discretion
- Polygonscan API integration details (authentication, rate limits, event parsing)
- Semantic rule parsing and validation logic
- Wallet trade event format and filtering
- Dashboard layout for 2 new strategy rows
- Test structure and fixtures

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- Phase 8 scan modules (scans/imbalance.py, news_snipe.py, correlated.py, time_decay.py) — proven pattern
- executor.py:_build_legs() — already has branches for 20+ opportunity types
- matcher.py — fuzzy matching for market pairing
- signal_aggregator.py — multi-source probability aggregation
- config.py — feature flags pattern (IMBALANCE_ENABLED, etc.)

### Patterns to Follow
- Two-stage scan: mid-price → CLOB refinement
- Config: _env_bool/_env_float with sensible defaults
- Executor: _build_legs() branch + _revalidate case
- CLI: --mode <name> in argparse choices
- Tests: unittest.mock, sys.modules stubs, class-based tests

</code_context>

<deferred>
## Deferred Ideas

- ML-based semantic relationship detection (use manual rules for now)
- On-chain MEV-style frontrunning (not appropriate for prediction markets)
- Cross-chain wallet tracking (Polymarket on Polygon only for now)
- Social graph analysis of whale traders (use static wallet list)
</deferred>
