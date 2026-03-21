---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
last_updated: "2026-03-21T09:10:37.162Z"
progress:
  total_phases: 4
  completed_phases: 3
  total_plans: 9
  completed_plans: 9
---

# STATE.md — Polymarket Arb Scanner

## Current Phase

- **Phase 3: Monitor & Optimize** — Plan 3 complete (phase complete)

## Current Plan Position

- **Phase:** 3
- **Plan:** 3 complete
- **Status:** Phase 03 complete
- **Tasks completed:** 2/2

## Session Log

- **2026-03-21**: Plan 03-03 executed. asyncio.PriorityQueue added to continuous.py for WS-triggered priority execution. reload_fee_rates() added to config.py. build_recommendations()/write_recommendations() added to backtest.py. Hourly fee reload, nightly backtest, weekly rebalance digest, and zero-opp detection wired into scan loop. 16 new tests. 1588 tests passing. OPTIMIZE-01, OPTIMIZE-02, OPTIMIZE-03 complete. Phase 3 complete.
- **2026-03-21**: Plan 03-02 executed. TradeDB.get_strategy_pnl() added. Three new API endpoints (/api/strategy-pnl, /api/balances, /api/rebalance) added to dashboard. Dashboard UI enhanced with per-strategy P&L horizontal bar chart and platform balances doughnut chart. 19 new tests. 1572 tests passing. MONITOR-01, MONITOR-04, OPTIMIZE-04, OPTIMIZE-05 complete.
- **2026-03-21**: Plan 03-01 executed. Per-strategy metrics labels (strategy key) wired into executor.py. AlertManager extended with LOSS_SPIKE (3x avg, 10-trade guard) and ZERO_OPP_PERIOD (5 consecutive empty scans) detection. 22 new tests. 1553 tests passing. MONITOR-02 and MONITOR-03 complete.
- **2026-03-21**: Plan 02-03 executed. Per-strategy integration tests (19 modes), run_all.py orchestrator, and RESULTS.md template created. Fixed BaseException catch bug in orchestrator. Phase 2 complete.
- **2026-03-21**: Plan 02-02 executed. Idempotency key generation, DB dedup (has_recent_trade), recovery dedup (dedup_skipped), and fee verification script created. 19 tests added. All 8 platforms verified. HARDEN-05 and HARDEN-02 complete.
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
- [Phase 02-harden-test]: Circuit breaker wired at outermost call level, outside tenacity, so circuit opens only after all retries exhausted
- [Phase 02-harden-test]: Module-level circuit breaker instances (not per-instance) ensure shared state across all callers of a given platform
- [Phase 02-harden-test]: Idempotency key uses minute bucket (Unix time // 60) so same order attempt within 60s maps to same key — window matches DB dedup window
- [Phase 02-harden-test]: has_recent_trade excludes skipped:* actions so recorded skips do not trigger false-positive dedup on next legitimate attempt
- [Phase 02-harden-test]: Recovery dedup marks as dedup_skipped (not failed) to distinguish intentional suppression from genuine failure
- [Phase 02-harden-test]: catch BaseException (not Exception) in run_all.py to handle pytest.skip — Skipped inherits from BaseException
- [Phase 03-monitor-optimize]: strategy label replaces platform label for all executor metrics — enables per-strategy P&L attribution in Prometheus
- [Phase 03-monitor-optimize]: loss spike guard requires 10+ trades in rolling window to prevent false positives on early data
- [Phase 03-monitor-optimize]: _trade_losses deque maxlen=20 gives rolling average over recent 20 trades only (older trades expire automatically)
- [Phase 03-monitor-optimize]: check_loss_spike uses strictly-greater-than comparison so exactly 3x avg does not fire (intent: spike not threshold)
- [Phase 03]: get_strategy_pnl uses opportunities.net_profit as proxy since trades table has no pnl column
- [Phase 03]: Priority queue uses negated execution priority so time-sensitive opps (StalePriceOpp 3.0, ResolutionSnipeOpp 2.5) dequeue first from min-heap
- [Phase 03]: reload_fee_rates() only touches fee rate globals — DRY_RUN and API keys are explicitly excluded
- [Phase 03]: Nightly backtest runs via run_in_executor to avoid blocking asyncio event loop

## Resume

- Phase 03 complete (all 3 plans done). Next: Phase 04 if it exists, otherwise project milestones review.
