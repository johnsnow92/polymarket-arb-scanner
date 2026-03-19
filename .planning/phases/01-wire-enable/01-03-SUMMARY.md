---
phase: 01-wire-enable
plan: 03
subsystem: infra
tags: [config, railway, deployment, feature-flags, market-making]

# Dependency graph
requires: []
provides:
  - MM config defaults matching user decision (2% spread, $500/market cap)
  - Feature flag comment block documenting Railway enablement pattern
  - Stale scan one-shot behavior documented in CLAUDE.md
  - Railway production configuration guide for all 4 features and 8 platforms
affects: [continuous.py, market_maker.py, Railway deployment]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Feature flags default false locally, enabled via Railway env vars in production"
    - "MM params: MM_MIN_SPREAD=0.02 (2%), MM_MAX_INVENTORY=500.0 ($500/market)"

key-files:
  created: []
  modified:
    - config.py
    - CLAUDE.md

key-decisions:
  - "MM_MIN_SPREAD default updated from 0.03 to 0.02 (2%) to match user production intent"
  - "MM_MAX_INVENTORY default updated from 50.0 to 500.0 ($500/market) to match user production intent"
  - "Feature flags (MM_ENABLED, SNAPSHOT_ENABLED, DYNAMIC_FEE_ENABLED, EVENT_MONITOR_ENABLED) remain false in config.py defaults for local dev safety — enabled via Railway env vars"
  - "Stale scan documented as one-shot no-op requiring --continuous mode for real detection"

patterns-established:
  - "Railway env var enablement: all optional features off locally, documented Railway overrides for production"

requirements-completed: [ENABLE-01, ENABLE-02, ENABLE-03, ENABLE-04, ENABLE-05, INTEG-05]

# Metrics
duration: 3min
completed: 2026-03-19
---

# Phase 01 Plan 03: Wire & Enable — Config Defaults and Railway Documentation Summary

**MM config defaults updated to production intent (2% spread, $500/market) and Railway env var guide created for all 4 feature flags and 8 trading platforms**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-19T21:09:53Z
- **Completed:** 2026-03-19T21:12:55Z
- **Tasks:** 2/3 completed (Task 3 is a human-action checkpoint — Railway configuration)
- **Files modified:** 2

## Accomplishments
- Updated MM_MIN_SPREAD default from 0.03 to 0.02 (matches user's 2% production decision)
- Updated MM_MAX_INVENTORY default from 50.0 to 500.0 (matches user's $500/market production decision)
- Added feature flag comment block in config.py documenting Railway enablement pattern for all 4 flags
- Documented stale scan one-shot limitation in CLAUDE.md (requires --continuous for real detection)
- Added Railway Production Configuration subsection to CLAUDE.md covering all 4 feature flags, MM tuning, dynamic fee config, and all 8 platform credential requirements

## Task Commits

Each task was committed atomically:

1. **Task 1: Update MM config defaults and document feature enablement** - `f248487` (chore)
2. **Task 2: Document stale scan behavior and Railway env vars in CLAUDE.md** - `851c319` (docs)
3. **Task 3: Configure Railway env vars for production** - PENDING (human-action checkpoint)

## Files Created/Modified
- `config.py` - MM_MIN_SPREAD default 0.03→0.02, MM_MAX_INVENTORY default 50.0→500.0, feature flag comment block added
- `CLAUDE.md` - Stale scan one-shot note added, Railway Production Configuration subsection added

## Decisions Made
- MM defaults match user decisions from planning: 2% spread, $500/market cap
- Feature flags remain false locally (safe for dev), Railway env vars enable them in production
- stale.py documented as requiring WebSocket/continuous mode — one-shot scan produces no results (informational only)
- POLYGON_RPC_URL default already set to `https://polygon-rpc.com` in config.py (public fallback); Railway should use Alchemy/Infura for reliability

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

**Task 3 is a human-action checkpoint.** See checkpoint details below — Railway env vars must be set manually in the Railway dashboard.

The following env vars need to be configured at Railway Dashboard -> Project -> Service -> Variables:

**Feature Flags:**
- `MM_ENABLED=true`
- `SNAPSHOT_ENABLED=true`
- `DYNAMIC_FEE_ENABLED=true`
- `EVENT_MONITOR_ENABLED=true`

**Market Making Tuning:**
- `MM_MIN_SPREAD=0.02`
- `MM_MAX_INVENTORY=500.0`

**Dynamic Fees:**
- `POLYGON_RPC_URL` — Polygon RPC endpoint (e.g., `https://polygon-rpc.com` or Alchemy/Infura URL)

**Platform Credentials (if not already set):**
- `BETFAIR_APP_KEY`, `BETFAIR_USERNAME`, `BETFAIR_PASSWORD`
- `SMARKETS_API_KEY`
- `SXBET_API_KEY`
- `MATCHBOOK_USERNAME`, `MATCHBOOK_PASSWORD`
- `GEMINI_API_KEY`, `GEMINI_API_SECRET`
- `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`

Note: `POLYMARKET_PRIVATE_KEY` and `KALSHI_API_KEY_ID`/`KALSHI_PRIVATE_KEY_PATH` should already be configured from initial deployment.

## Next Phase Readiness
- Config defaults are production-ready — Railway env vars will override as needed
- CLAUDE.md is the authoritative reference for Railway deployment configuration
- Once Railway env vars are configured (Task 3), all 4 features will be active in production
- No code changes needed beyond config defaults — all features are already wired in continuous.py

---
*Phase: 01-wire-enable*
*Completed: 2026-03-19*

## Self-Check: PASSED

- `config.py` exists and verified: MM_MIN_SPREAD=0.02, MM_MAX_INVENTORY=500.0
- `CLAUDE.md` exists with stale scan note and Railway Production Configuration section
- Commit f248487 exists (Task 1)
- Commit 851c319 exists (Task 2)
- All 1471 tests pass (no regressions)
