# Phase 4: Go Live - Context

**Gathered:** 2026-03-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Progressive deployment from safest (Layer 1) to riskiest (Layer 4) on Railway. 7-day validation against the project's 3 success criteria. This is a deployment runbook with human-action checkpoints — not a coding phase.

Requirements: LIVE-01 through LIVE-06.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — this is a deployment/operational phase. Key constraints from ROADMAP:
- Start with `MAX_TRADE_SIZE` at 10% of target
- Keep `DAILY_LOSS_LIMIT` conservative until Layer 1 proven
- Manual kill switch via Railway dashboard (stop deployment)
- `DRY_RUN=true` fallback — one env var change to pause all execution
- Layer enablement timeline: L1 Day 0, L2 Day 2, L3 Day 4, L4 Day 5, validation Day 5-12

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `Dockerfile` — Production image (python:3.12-slim), runs `scanner.py --continuous`
- `config.py` — All env vars with defaults, `validate_config()` at import
- `dashboard.py` — Health check at `/healthz` for Railway
- `recovery.py` — Crash reconciliation on startup
- All feature flags: `DRY_RUN`, `MM_ENABLED`, `SNAPSHOT_ENABLED`, `DYNAMIC_FEE_ENABLED`, `EVENT_MONITOR_ENABLED`

### Established Patterns
- Railway auto-deploys on push to `master`
- `DATA_DIR` env var for persistent SQLite
- Webhook alerts via `WEBHOOK_URL`

### Integration Points
- Railway Dashboard → env vars control all behavior
- `.github/workflows/test.yml` → CI gate before deploy

</code_context>

<specifics>
## Specific Ideas

No specific requirements — deployment runbook phase.

</specifics>

<deferred>
## Deferred Ideas

None — deployment phase stays within scope.

</deferred>
