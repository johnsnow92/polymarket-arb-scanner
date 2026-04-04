---
phase: 06-monitor-harden
plan: 04
subsystem: monitoring
tags: [websocket, heartbeat, staleness, feed-health, revalidation]

requires:
  - phase: 06-03
    provides: "Zero-opportunity and loss-streak alerting foundation"

provides:
  - "FeedManager heartbeat monitoring with _last_message_time tracking"
  - "Stale feed detection and price marking via _stale flag in cache"
  - "Executor revalidation rejecting prices from stale feeds"
  - "Async background task monitoring feed health every 5 seconds"
  - "Integration test suite covering feed staleness and executor rejection"

affects: [06-05, 06-06, continuous-mode, production-reliability]

tech-stack:
  added: []
  patterns:
    - "Heartbeat monitoring via _last_message_time tracking"
    - "In-memory stale flag propagation (_stale: true/false)"
    - "Async background task for non-blocking health checks"
    - "Early-return pattern in revalidation for feed_stale rejection"

key-files:
  created:
    - "tests/test_ws_stale_detection.py"
  modified:
    - "ws_feeds.py"
    - "executor.py"
    - "continuous.py"

key-decisions:
  - "Stale detection frequency: 5 seconds (faster than 30s threshold for quicker response)"
  - "_stale flag stored in price cache entries, cleared on recovery (no persistence needed)"
  - "Stale check performed FIRST in revalidation (before any other validations)"
  - "Background task runs in parallel to main scan loop (non-blocking via asyncio.create_task)"
  - "Platform independence: staleness of one platform doesn't affect others"

requirements-completed: [HARD-01]

patterns-established:
  - "Pattern: Async background health monitoring without blocking main loop"
  - "Pattern: Per-entry stale flag in shared cache with platform-level timeout logic"
  - "Pattern: Early rejection in validation chain (stale check first)"

duration: 45min
completed: 2026-04-04
---

# Phase 06: Monitor-Harden Plan 04 Summary

**WebSocket heartbeat monitoring with 30-second feed staleness detection, stale price rejection in executor revalidation, and non-blocking async health monitoring task**

## Performance

- **Duration:** ~45 min (including test fixes)
- **Completed:** 2026-04-04
- **Tasks:** 4 (all completed)
- **Files modified:** 3
- **Tests:** 12 (all passing)

## Accomplishments

- **FeedManager heartbeat monitoring:** `_last_message_time` dict tracks message timestamps per platform; `mark_stale_feeds()` marks prices as `_stale: true` when no message received in 30 seconds; `is_feed_healthy()` helper checks feed status
- **Executor stale rejection:** All revalidation methods (`_revalidate_binary`, `_revalidate_negrisk`, `_revalidate_cross`, `_revalidate_multi_cross`) check `_stale` flag FIRST and return `(False, 0.0, "feed_stale")` if stale (8 stale checks across revalidation paths)
- **Continuous loop integration:** Async task `_monitor_feed_staleness()` spawned with `asyncio.create_task()`, calls `mark_stale_feeds()` every 5 seconds, properly cancelled on shutdown
- **Comprehensive test coverage:** 12 test cases covering feed health detection, stale flag marking/clearing, executor rejection, multi-platform independence, and idempotency

## Task Commits

1. **All tasks combined in single commit:**
   - `1056e6b` - `feat(06-04): add WebSocket heartbeat monitoring and stale feed detection`
   - Includes: ws_feeds.py heartbeat monitoring, executor stale checks, continuous.py monitoring task, test suite

## Files Created/Modified

- **ws_feeds.py** (modified)
  - Added `_last_message_time` dict (line 93, already present)
  - Added `mark_stale_feeds(stale_threshold_seconds=30.0)` method (line 225-264)
    - Iterates platforms, checks elapsed time since last message
    - Marks prices `_stale: true` when `now - last_msg_time > 30s`
    - Clears `_stale: false` when feed recovers
    - Logs stale warnings and recovery info
  - Added `is_feed_healthy(platform, threshold_seconds=30.0)` method (line 266-276)
    - Helper returning `bool` for quick health checks

- **executor.py** (modified)
  - `_revalidate_binary()` (lines 524-529)
    - Check `cached_yes.get("_stale", False)` → return `(False, 0.0, "feed_stale")`
    - Check `cached_no.get("_stale", False)` → return `(False, 0.0, "feed_stale")`
  - `_revalidate_negrisk()` (lines 583-589)
    - Check stale for each cached token before proceeding
  - `_revalidate_cross()` (lines 628-634)
    - Check stale for polymarket tokens and kalshi ticker separately
  - `_revalidate_multi_cross()` (lines 653-659)
    - Check stale for each outcome leg before revalidation
  - All checks at INFO log level with `"Skipping revalidation: ... stale for >30s"` messages

- **continuous.py** (modified)
  - Added `_monitor_feed_staleness()` async function (lines 822-836)
    - Runs while `not shutdown_event.is_set()`
    - Calls `feed_manager.mark_stale_feeds(stale_threshold_seconds=30.0)` every 5 seconds
    - Try/except with warning logging on failure, 5-second retry
  - Modified `_continuous_loop()` (lines 837-850, 1577-1585)
    - Create task: `stale_monitor_task = asyncio.create_task(_monitor_feed_staleness())`
    - Cancel on shutdown with proper exception handling

- **tests/test_ws_stale_detection.py** (created)
  - 12 test cases total
  - **TestFeedHealthMonitoring class (6 tests)**
    - `test_fresh_feed_is_healthy` - Feed <30s since message returns True
    - `test_stale_feed_is_unhealthy` - Feed >30s since message returns False
    - `test_mark_stale_feeds_sets_flag` - Stale feeds marked `_stale: true`
    - `test_feed_recovery_clears_flag` - `_stale: false` when feed recovers
    - `test_multiple_platforms_independent` - One platform stale doesn't affect others
    - `test_mark_stale_feeds_idempotent` - Safe to call repeatedly
  - **TestExecutorStaleRejection class (6 tests)**
    - `test_executor_rejects_stale_opportunity_binary` - Binary opp rejected when stale
    - `test_executor_accepts_fresh_opportunity_binary` - Binary opp accepted when fresh
    - `test_revalidation_logs_stale_skip` - Logging contains "stale" message
    - `test_executor_rejects_stale_cross_platform` - Cross-platform opp rejected when stale
    - `test_executor_rejects_stale_multi_cross` - Multi-cross opp rejected when any leg stale
    - `test_stale_flag_default_false` - Missing `_stale` flag defaults to healthy

## Verification Results

All success criteria met:

- ✅ `pytest tests/test_ws_stale_detection.py -v` → **12 passed in 0.31s**
- ✅ `grep -n "def mark_stale_feeds" ws_feeds.py` → Line 225 (method exists)
- ✅ `grep -n "if.*_stale" executor.py | wc -l` → 8 stale checks across revalidation paths
- ✅ `grep -n "_monitor_feed_staleness\|mark_stale_feeds" continuous.py` → Lines 822, 831, 848 (monitoring task integrated)
- ✅ Heartbeat check frequency: 5 seconds (every 5 seconds in `_monitor_feed_staleness`)
- ✅ Stale detection latency: 30 seconds max (threshold: `>30s` without message)
- ✅ Thread-safe operations confirmed (uses `_price_cache_lock` in `mark_stale_feeds`)

## Decisions Made

- **Frequency: 5 seconds (not 10)** - Faster detection of network issues provides better safety margin before 30-second stale threshold
- **Platform independence** - Each platform tracked separately; one stale feed doesn't block others
- **Early rejection in revalidation** - Stale check performed FIRST (before any profit recalculation) to fail fast
- **In-memory only** - `_stale` flag stored in price cache, not persisted; clears on restart or feed recovery
- **Non-blocking async task** - Background monitoring runs in parallel via `asyncio.create_task()` without blocking main scan loop

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added total_cost and _layer fields to executor test**
- **Found during:** Task 4 (test_executor_accepts_fresh_opportunity_binary)
- **Issue:** Test initially failed with "assert False is True" because `_get_revalidation_threshold()` requires `total_cost` and `_layer` fields in opportunity dict to calculate proper threshold
- **Fix:** Added `"total_cost": "$0.50"` and `"_layer": 1` to opp dict in test; increased profit from 0.01 to 0.05 to ensure it passes threshold calculation
- **Files modified:** tests/test_ws_stale_detection.py
- **Verification:** All 12 tests now pass
- **Committed in:** 1056e6b (included in main commit)

---

**Total deviations:** 1 auto-fixed (missing critical test setup)
**Impact on plan:** Auto-fix essential for test correctness. No scope creep — follows existing revalidation pattern from production code.

## Issues Encountered

None - plan executed smoothly after fixing test data structure.

## Threat Model Mitigations

Per plan threat_model:

| Threat ID | Category | Mitigation |
|-----------|----------|-----------|
| T-06-13 | DoS: Feed falsely marked stale | Stale flag cleared on recovery (any message); logs stale/recovery cycles for alerting |
| T-06-14 | Tampering: Manual stale marking | In-memory flag; no persistence; reset on restart |
| T-06-15 | Info disclosure: Feed health | Feed health acceptable to expose; no secrets revealed |

## Next Phase Readiness

- ✅ WS heartbeat monitoring ready for production (HARD-01 satisfied)
- ✅ Executor integration prevents losses from stale prices
- ✅ Foundation ready for Plan 05 (hedger validation) and Plan 06 (credential health)
- No blockers or concerns identified

---

*Phase: 06-monitor-harden*
*Plan: 04*
*Completed: 2026-04-04*
