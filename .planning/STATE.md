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

## Decisions
- MM defaults set to production intent: MM_MIN_SPREAD=0.02 (2%), MM_MAX_INVENTORY=500.0 ($500/market)
- Feature flags remain false in config.py defaults (local dev safety); Railway env vars enable in production
- Stale scan is a no-op in one-shot mode — requires --continuous for real detection

## Resume
- File: `.planning/phases/01-wire-enable/01-03-PLAN.md`
- Task: Task 3 — Configure Railway env vars (human-action checkpoint)
- Signal: Type "configured" once Railway vars are set, or "skip" to defer
