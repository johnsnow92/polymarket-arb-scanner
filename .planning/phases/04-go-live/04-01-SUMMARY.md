# Plan 04-01 Summary — Deploy Layer 1

**Status:** Partial (auto tasks complete, human checkpoints pending)
**Requirements:** LIVE-01, LIVE-02

## Completed

- **Task 1 (auto):** Created `scripts/go_live_check.py` and `scripts/pnl_report.py`
  - Pre-flight check: validates /healthz, /status, /metrics endpoints
  - P&L report: per-strategy breakdown, win rates, success criteria validation
  - Both stdlib-only, no pip deps

## Pending (Human Action)

- **Task 2:** Configure Railway env vars for Layer 1 full-auto (`DRY_RUN=false`, `EXECUTION_MODE=full-auto`, `MAX_TRADE_SIZE=3.0`, `DAILY_LOSS_LIMIT=5.0`)
- **Task 3:** Monitor Layer 1 for 48 hours (check at 6h/12h/24h/48h marks)

## Commits

- `c39fc81` feat(04-01): add go-live pre-flight check and P&L report scripts

## Deviations

None.
