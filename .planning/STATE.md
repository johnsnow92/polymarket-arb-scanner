# STATE.md — Polymarket Arb Scanner

## Current Phase
- **Phase 1: Wire & Enable** — Plan 03 partially executed; paused at Task 3 (Railway human-action checkpoint)

## Current Plan Position
- **Phase:** 01-wire-enable
- **Plan:** 03
- **Status:** Checkpoint — awaiting Railway env var configuration (Task 3)
- **Tasks completed:** 2/3

## Session Log
- **2026-03-19**: Phase 1 context gathered. Decisions captured for fee routing (dual-layer, all cross-platform), MM params ($500/market, 2% spread, all platforms), feature enablement (all 4 flags), bankroll refresh (timer + post-trade, all 8 platforms).
- **2026-03-19**: Plan 01-03 executed. Tasks 1-2 complete. Config defaults updated (MM_MIN_SPREAD=0.02, MM_MAX_INVENTORY=500.0). CLAUDE.md updated with stale scan docs and Railway production guide. Paused at Task 3 (human-action: configure Railway env vars).
- **2026-03-19**: Plan 01-02 executed. All 3 integration gaps closed: MM dry_run hardcode fixed (cli.py), Kalshi resolution scan added (continuous.py), bankroll refresh wired (timer + post-trade). 10 new tests added. 1484 tests passing.
- **2026-03-19**: Plan 01-01 executed. find_lowest_fee_path wired into scan_cross_platform and scan_cross_all (scan-time _fee_path hint). Executor _build_legs Cross branch re-validates fee path and uses result for optimal routing, falls back to default when stale. 7 new tests added (TestFeePath + TestFeePathExecution). 1488 tests passing.

## Decisions
- MM defaults set to production intent: MM_MIN_SPREAD=0.02 (2%), MM_MAX_INVENTORY=500.0 ($500/market)
- Feature flags remain false in config.py defaults (local dev safety); Railway env vars enable in production
- Stale scan is a no-op in one-shot mode — requires --continuous for real detection
- executor.dry_run (not hardcoded True) controls MarketMaker dry_run in one-shot mode
- Kalshi resolution uses kalshi_data[1] (markets_by_event dict) flattened to flat list for scan_resolution_snipes
- Bankroll refresh uses _fetch_balances("Cross") to get all 8 platform balances; timer sets last refresh time even on failure to prevent retry storms
- Fee routing dual-layer: scan attaches _fee_path hint post-CLOB-refinement; executor re-validates at trade time using stored platform pair and prices
- _fee_path is absent (not None) on opps when find_lowest_fee_path returns None — absence = use default routing
- Cross-all price parsing uses per-available-value dicts (not requiring both platforms in each dict) since each opp stores only one YES and one NO price

## Resume
- File: `.planning/phases/01-wire-enable/01-03-PLAN.md`
- Task: Task 3 — Configure Railway env vars (human-action checkpoint)
- Signal: Type "configured" once Railway vars are set, or "skip" to defer
