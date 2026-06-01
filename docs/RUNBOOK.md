# Operations Runbook

> **Owner:** Jonathon Tamm · **Review cadence:** monthly, and after any deploy incident.

## Deploy (Railway)
- Railway auto-deploys on **push to `master`** via GitHub integration (Docker, `python:3.12-slim`). Entrypoint: `python scanner.py --continuous`.
- **A push to `master` restarts the live worker even for docs-only changes.** Batch changes and merge in a controlled window; avoid merging while opportunities are mid-execution.
- Required production env vars:
  - `DASHBOARD_HOST=0.0.0.0` (Railway edge healthcheck must reach the container)
  - `DASHBOARD_PASS=<strong secret>` (required whenever host is non-loopback — `validate_config()` enforces)
  - Feature flags as desired: `MM_ENABLED`, `SNAPSHOT_ENABLED`, `DYNAMIC_FEE_ENABLED`, `EVENT_MONITOR_ENABLED` (all default false)
  - All platform credentials (see `PLATFORM-MATRIX.md`)
- Health check: `GET /healthz` on 8080. The Dockerfile healthcheck reads Railway's `PORT` before falling back to 8080 (PR #19).
- Persistent state: `DATA_DIR` holds `trades.db` + `snapshots.db`.
- IBKR needs a reachable IB Gateway socket — not viable from Railway without a persistent gateway host.

## Post-deploy checklist (run before trusting live trading)
1. `/healthz` returns 200.
2. Dashboard `/status` shows the continuous loop alive.
3. WS feeds connected (Polymarket + Kalshi).
4. Exactly one worker (no duplicate).
5. Balances / open orders reconciled by `recovery.py:reconcile_orphaned_positions()` (runs on startup).
6. `validate_config()` passed (startup log) and execution flags match intent.

## Safe feature-flag enablement
Enable one flag at a time, with `DRY_RUN=true` first, watch a full cycle, then promote. Never flip a flag and `DRY_RUN=false` in the same deploy.

## Observability acceptance contract
Per failure mode: current signal, alert, owner. **Gaps are marked** — they feed `ROADMAP.md`, not silently omitted.

| Failure mode | Log/metric | Alert (`AlertType`) | Owner | Status |
|---|---|---|---|---|
| Daily loss breach | `metrics` P&L + log | `DAILY_LOSS_LIMIT` (CRITICAL) | JT | ✅ |
| Loss streak / spike | log | `LOSS_STREAK`, `LOSS_SPIKE` | JT | ✅ |
| Position limit | log | `POSITION_LIMIT` | JT | ✅ |
| WS reconnect loop | log | `WS_DISCONNECT` | JT | ✅ alert exists; **reconnect-loop threshold tuning = gap** |
| Order reject / partial fill | executor log | `EXECUTION_FAILURE` | JT | ✅ alert; **per-partial-fill metric = gap** |
| Scan failure | log | `SCAN_FAILURE` | JT | ✅ |
| Auth / rate-limit failure | log | `CREDENTIAL_FAILURE` | JT | ⚠️ rate-limit (429) has no dedicated alert — **gap** |
| Low balance | log | `BALANCE_LOW` | JT | ✅ |
| Zero-opp period | log | `ZERO_OPP[_PERIOD]` | JT | ✅ |
| DB write failure | exception → Sentry | — | JT | ⚠️ **no dedicated alert — gap** |
| Process crash | Sentry (PR #26) | — | JT | ✅ Sentry; **no uptime/heartbeat alert — gap** |
| P&L / daily-loss breaker trip | `alerting.py` | `DAILY_LOSS_LIMIT` | JT | ✅ |
| Kill-switch / DRY_RUN flip | startup log | — | JT | ⚠️ **no explicit audit event — gap** |

Metrics are exposed Prometheus-style (`metrics.py:MetricsCollector`).

## Rollback runbook (bad deploy)
1. **Stop the bleeding:** set `DRY_RUN=true` in Railway env and redeploy — halts live order placement immediately.
2. **Pause/cancel pending orders:** check dashboard / `trades.db`; cancel open orders on the affected platform(s) via their console or client.
3. **Revert code:** `git revert <bad-commit>` (or redeploy the prior known-good commit) to `master`.
4. **Reconcile positions:** restart triggers `recovery.py:reconcile_orphaned_positions()`; verify open positions in `trades.db` match each platform's actual positions.
5. **Env snapshot:** keep a copy of Railway env vars before any window so you can restore exact state.
6. Confirm via the post-deploy checklist before re-enabling live trading.
