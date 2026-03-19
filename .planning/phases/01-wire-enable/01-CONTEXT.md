# Phase 1: Wire & Enable - Context

**Gathered:** 2026-03-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Connect all existing code into the live pipeline and flip feature flags for production readiness. All 20 strategies should be reachable from continuous mode with proper configuration. No new strategy implementation — this is integration and enablement of existing code.

Requirements: INTEG-01 through INTEG-05, ENABLE-01 through ENABLE-05.

</domain>

<decisions>
## Implementation Decisions

### Fee Routing Placement
- **Dual-layer approach**: scan attaches `_fee_path` hint, executor re-validates at trade time
- Scan-time: `find_lowest_fee_path()` runs during scanning, attaches single best path as `_fee_path` key on opportunity dict
- Execution-time: executor calls `find_lowest_fee_path()` again with fresh fee data to confirm or override the scan hint
- **Scope**: apply to ALL cross-platform opportunities, not just multi-path arbs
- **Path data**: best path only (single `_fee_path` entry), not ranked list — keeps opportunity dicts lean

### Market Making Parameters
- **Platforms**: enable MM on ALL platforms with bid/ask support (not just Polymarket)
- **Inventory limit**: $500 per market initial cap
- **Minimum spread**: 2% minimum spread width for quotes (e.g., bid 0.48, ask 0.52)
- `MM_ENABLED` flag gates the entire engine — when true, all supported platforms participate

### Feature Enablement Strategy
- **Enable all 4 flags simultaneously** in this phase: `MM_ENABLED`, `SNAPSHOT_ENABLED`, `DYNAMIC_FEE_ENABLED`, `EVENT_MONITOR_ENABLED`
- Rationale: Phase 1 makes everything reachable; Phase 2 validates with real data. `DRY_RUN` stays true so nothing trades yet.
- **Config defaults**: Claude's discretion on whether to change `config.py` defaults or leave as false with Railway env var overrides only

### Bankroll Refresh
- **Trigger**: both timer-based (every 5 minutes) AND post-trade (immediate refresh after any trade execution)
- **Scope**: query ALL 8 platform balances, not just platforms with active positions — total capital gives most accurate Kelly sizing
- Wire `update_bankroll()` in `continuous.py` with both trigger paths

### Claude's Discretion
- Whether to change `config.py` defaults to `true` or keep as `false` with Railway env var overrides (leaning toward keeping defaults safe for local dev)
- Exact error handling for platform balance queries that fail during bankroll refresh (skip platform vs use cached value)
- Internal implementation of fee path hint attachment (new helper function vs inline in scan modules)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Integration targets
- `fees.py` lines 948-1004 — `find_lowest_fee_path()` function to wire into scans and executor
- `cli.py` line ~488 — MarketMaker `dry_run=True` hardcode to fix
- `continuous.py` lines 1009-1017 — Resolution scan that needs Kalshi addition
- `position_sizer.py` lines 328-337 — `update_bankroll()` to wire into continuous mode

### Configuration
- `config.py` lines 231, 236, 243, 292 — Feature flag defaults (`DYNAMIC_FEE_ENABLED`, `EVENT_MONITOR_ENABLED`, `MM_ENABLED`, `SNAPSHOT_ENABLED`)

### Architecture context
- `.planning/codebase/ARCHITECTURE.md` — Layer structure, data flow, opportunity dict contract
- `.planning/codebase/CONVENTIONS.md` — Coding patterns, naming, opportunity dict standardization

### Requirements
- `.planning/REQUIREMENTS.md` — INTEG-01 through INTEG-05, ENABLE-01 through ENABLE-05 acceptance criteria

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `find_lowest_fee_path()` in `fees.py` — fully built, tested, returns optimal platform pair with fee breakdown
- `update_bankroll()` in `position_sizer.py` — accepts total balance, recalculates Kelly fractions
- `market_maker.py` (528 lines) — complete MM engine with QuoteEngine, InventoryTracker, QuoteManager
- `scan_resolution_snipes()` in `scans/resolution.py` — platform-agnostic, accepts any market list + platform name
- All 10 platform API clients have `get_balance()` methods for bankroll queries

### Established Patterns
- **Opportunity dict with `_` prefix keys**: internal metadata like `_token_ids`, `_clob_depth`. New `_fee_path` key follows this pattern.
- **Two-stage scan**: mid-price → CLOB refinement. Fee path hint attaches during mid-price stage.
- **ThreadPoolExecutor parallelism**: used for data fetching and scanning. Bankroll refresh can use same pattern.
- **Config via env vars**: all config flows through `config.py` with `_env_bool()`, `_env_float()`, `_env_int()` helpers.

### Integration Points
- `executor.py:_build_legs()` — dispatcher that converts opportunity dict to execution legs; must read `_fee_path` for routing
- `continuous.py` main loop — add bankroll refresh timer alongside existing settlement check timer
- `scans/cross.py` — cross-platform scan where fee path hints are attached
- `cli.py:_run_oneshot()` — market making mode dispatch, fix dry_run hardcode

</code_context>

<specifics>
## Specific Ideas

- User wants aggressive MM settings ($500/market, all platforms) — not a conservative crawl-walk-run on MM
- Fee routing on ALL cross-platform opportunities, even 2-platform pairs — maximize savings
- Dual-layer fee routing (scan + execution) for both speed and accuracy
- Bankroll queries across ALL 8 platforms for accurate total capital picture

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-wire-enable*
*Context gathered: 2026-03-19*
