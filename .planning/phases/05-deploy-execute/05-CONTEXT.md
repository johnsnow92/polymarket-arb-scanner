# Phase 5: Deploy & Execute - Context

**Gathered:** 2026-04-01
**Status:** Ready for planning

<domain>
## Phase Boundary

Unblock production execution and get the first profitable trade. Fix revalidation to use layer-specific auto-tuned floors, route qualifying orders as maker (limit), verify all 8 platform fee rates against 2026 schedules, and execute at least one autonomous profitable round-trip trade.

Requirements: EXEC-01, EXEC-02, EXEC-03, EXEC-04, EXEC-07.

</domain>

<decisions>
## Implementation Decisions

### Revalidation Calibration
- **D-01:** Auto-tune revalidation floors from dry-run data. Deploy with DRY_RUN=true for 72 hours. Log every revalidation decision with: candidate ROI at scan time, ROI at revalidation, delta, rejection reason, time elapsed, and layer tag. Compute 80th-percentile price drift per layer after sufficient samples (~50+ per layer).
- **D-02:** Use roadmap values (2% L1, 5% L2, 3% L3, 10% L4) as initial floors while collecting calibration data. Replace with auto-tuned values after 72h observation period.
- **D-03:** Tag layer in opportunity dict — each scan module sets `opp["_layer"]` = 1-4 based on strategy type. Executor reads `_layer` for floor lookup. Strategy-to-layer mapping per CLAUDE.md scope definition.
- **D-04:** 72-hour minimum dry-run calibration period before enabling live trading. Target pass rate: 5-30% (per PITFALLS.md).

### Maker Routing
- **D-05:** Route qualifying orders as limit (maker) on Polymarket and Kalshi. Cancel and skip unfilled orders after timeout — no taker fallback.

### Claude's Discretion
- Maker order aggressiveness per strategy layer (Claude decides based on urgency and layer type — Layer 1 time-sensitive arbs more aggressive, Layer 3-4 more passive)
- Specific timeout duration for unfilled maker orders (5-10s range)

### Fee Verification
- **D-06:** Manual audit of all 8 platform fee rates against current 2026 platform fee pages + automated pytest assertions codifying correct rates. CI catches future drift.
- **D-07:** Hardcoded fee rates in fees.py with env-var overrides (e.g., POLYMARKET_TAKER_FEE). Allows hotfixing a fee change via Railway env vars without deploy.

### First Trade Criteria
- **D-08:** All strategy layers eligible for first live trades simultaneously. $5 max trade size initially.
- **D-09:** $25/day daily loss limit during initial live trading period (5 losing trades at $5 before circuit breaker).
- **D-10:** Success = at least one round-trip trade with net positive P&L recorded in trades.db.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Pitfalls & Calibration
- `.planning/research/PITFALLS.md` — Critical pitfalls including revalidation calibration (Pitfall 1), oracle mismatch (Pitfall 2), partial fill exposure (Pitfall 3). Sets the 48-72h dry-run requirement and 80th-percentile calibration method.

### Requirements & Roadmap
- `.planning/REQUIREMENTS.md` — EXEC-01 through EXEC-04, EXEC-07 are this phase's requirements
- `.planning/ROADMAP.md` §Phase 5 — Success criteria (5 items), layer-specific floor values

### Architecture & Conventions
- `.planning/codebase/ARCHITECTURE.md` — Data flow, execution layer structure
- `.planning/codebase/CONVENTIONS.md` — Opportunity dict standardization, `_build_legs` dispatcher pattern, coding style

### Prior Phase Context
- `.planning/phases/04-go-live/04-CONTEXT.md` — Phase 4 deployment decisions (layer enablement timeline, DRY_RUN fallback, kill switch)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `executor.py` — Already has `_revalidate()` dispatcher, `revalidation_adaptive` flag, `revalidation_min_floor` parameter. Needs layer-aware floor lookup.
- `config.py` — `MIN_NET_ROI` env var exists (currently 0). Layer-specific vars (REVAL_FLOOR_L1, etc.) can follow same `_env_float()` pattern.
- `fees.py` — All 8 platform fee calculators exist. Need audit against 2026 rates and env-var override wrappers.
- `tests/integration/verify_fees.py` — Existing fee verification test scaffold.

### Established Patterns
- Opportunity dicts flow through system with `_`-prefixed internal keys. Adding `_layer` follows this convention.
- Config precedence: CLI args > env vars > defaults in `config.py`. Fee overrides follow this pattern.
- Two-stage detection: mid-price scan → CLOB refinement. Layer tag added during scan stage 1.

### Integration Points
- `executor.py:_revalidate()` — Entry point for layer-aware floor lookup
- `executor.py:_build_legs()` — Where maker vs taker routing decision happens
- Each `scans/*.py` module — Where `_layer` tag gets set on opportunity dicts
- `config.py` — Where new REVAL_FLOOR_* and fee override env vars are defined
- Railway Dashboard — Where env vars control calibration and live trading behavior

</code_context>

<specifics>
## Specific Ideas

- PITFALLS.md recommends: log every revalidation decision with candidate ROI, revalidation ROI, delta, rejection reason, and elapsed time. Use this data to compute 80th-percentile drift.
- Pass rate target: 5-30%. If 0% = too tight, if >50% = too loose.
- Bimodal drift distribution = need per-layer thresholds (which we're building).

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 05-deploy-execute*
*Context gathered: 2026-04-01*
