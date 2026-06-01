# Changelog

> **Owner:** Jonathon Tamm · **Review cadence:** per merged PR to `master`.
> Format: [Keep a Changelog](https://keepachangelog.com/). Seeded from GitHub PR metadata (`gh`); maintain on each merge.

## [Unreleased]
- **Docs:** content-audit register + platform-research artifacts (`docs/audit/`); WS-1 doc truth-alignment (fees, status block, strategy counts, `--mode` list); WS-2 process docs (this file, ARCHITECTURE, PLATFORM-MATRIX, RISK-POLICY, RUNBOOK, BACKTESTING, SECURITY, CONTRIBUTING, TASK_CONTRACT, ROADMAP, PRD).
- **In-flight:** Strategy #20 backtesting-driven tuning loop (`scripts/tune.py`, `config.load/apply_backtest_recommendations`) — implemented + tested, not yet production-wired.

## 2026-05-20 — Sprints 3–5
- #32 Sprint 5 — fix 5 isolation-mode `test_executor.py` failures (autouse mock cleanup).
- #31 Sprint 4 — executor branches for NWayArb / LeadLagMM / defensive observability opps.
- #30 Sprint 3 — wire Sprint 2 scans into continuous mode + WS-tick feeds.

## 2026-05-18 — Sprints 1–2
- #29 Sprint 2 — NWayArb / LeadLagMM / ToxicFlowPause / VolatilityAdjustedMM scans.
- #28 Sprint 1 — dashboard XSS fix + `tests/test_dashboard_ui.py`.

## 2026-05-13 — Strategy expansion & monitoring
- #26 Wire Sentry error monitoring at scanner entry points.
- #24/#23 Untrack files now in `.gitignore`; exclude `.firecrawl/`, `.worktrees/`, `decisions.jsonl`.
- #22 Implement 20 additional arbitrage strategies (internal #30–#49 series — taxonomy reconciliation pending, see `docs/strategy-framework-v2.md`).

## 2026-05-09 to 2026-05-11 — v2 framework adoption & arbgrid rename
- #20 Apply orphaned PR #18 review fixes (test isolation, validation tests, doc reconciliation).
- #19 Dockerfile healthcheck reads Railway's `PORT` before falling back to 8080.
- #18 v2 framework adoption, arbgrid rename, Phase 1 quick wins, README.
- #17 Rate-limiter audit + dedicated test coverage.
- #16 `scripts/tune.py` wrapper for #20.
- #15 Correlated-pairs auto-tracker + Stage 2 refiner (#29).
- #14 Whale-copy Stage 2 refiner with live CLOB checks (#28b).
- #12 First-class Stage 2 refiners for #26 time-decay and #27 news-snipe.
- #11 v2 framework adoption + arbgrid rename + Phase 1 quick wins.
- #10 First-class coverage for #9, #11, #12, #18.

## 2026-04-26 to 2026-04-30 — Cross-platform event-driven detection
- #9 Metrics: pass value as kwarg to `_metrics.set`.
- #8 Cross-pair counters + gauge for WS-driven Cross path.
- #7/#6 Persistent `CrossPairIndex` for event-driven Cross detection (Phases 1–2).
- #5 Per-stage timing logs to identify scan bottlenecks.
- #4 `suggest_min_roi` correctness on high win-rate.
- #3 Restore green CI on `master`.
- #2 Kalshi nested-markets fetch + TTL cache (−30% scan latency).
- #1 Surface bankroll-refresh state and failures.
