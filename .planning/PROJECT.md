# PROJECT.md — Polymarket Arb Scanner

## What This Is

A 24/7 automated prediction market arbitrage bot deployed on Railway. Scans 8 trading platforms + 2 signal sources for 20 strategy types across 5 risk layers. Full-stack: detection, revalidation, execution, risk management, market making, monitoring, alerting, backtesting, and crash recovery.

## Core Value

Automated profit extraction from prediction market inefficiencies across platforms.

## Current Milestone: v2.0 Profitable Trading & Strategy Expansion

**Goal:** Get the bot actually making money while simultaneously expanding and hardening the strategy set.

**Target features:**
- Deploy revalidation fix and tune thresholds until trades execute
- Fund platform accounts and get capital flowing
- Monitor, iterate, and validate all 20 existing strategies work in production
- Research and implement new prediction market strategies
- End-to-end profitability: net positive P&L over sustained period

## Current State

**Shipped:** v1.0 — Production-Ready Automated Trading (2026-04-01)
**Codebase:** 24,390 LOC production + 21,907 LOC tests = 46,297 LOC Python
**Tests:** 1,592+ passing (pytest)
**Deployment:** Railway (Docker, continuous mode, health check /healthz)
**Status:** Code-complete. Bot runs 24/7 but production execution blocked by revalidation tuning. Fix committed locally (API error tolerance, widened WS cache, lowered floor), pending deployment.

### What v1.0 Delivered

- Fee-optimal routing wired into cross-platform scans + executor
- Circuit breakers on all 8 platform APIs
- SHA-256 idempotency keys + DB dedup + crash recovery dedup
- Per-strategy Prometheus metrics at /metrics
- Loss spike + zero-opportunity alerting via webhooks
- Live dashboard with strategy P&L charts and platform balances
- Priority execution queue for time-sensitive strategies
- Nightly backtest feedback loop with threshold recommendations
- Dynamic fee reload without restart
- Go-live pre-flight scripts and 7-day validation report

### Known Gaps (accepted at milestone completion)

- LIVE-01 through LIVE-06: Operational requirements needing real trading time
- HARDEN-01: 18/19 integration tests skip without live credentials
- HARDEN-04: No Retry-After header parsing (reactive 429 handling only)
- Production execution: 100% revalidation rejection (fix committed, not deployed)

## Platforms

| Platform | Auth | Trade | Status |
|----------|------|-------|--------|
| Polymarket | Ethereum CLOB | Buy + Sell | BUILT |
| Kalshi | RSA-PSS headers | Buy + Sell | BUILT |
| Betfair | SSO + API key | Back + Lay | BUILT |
| Smarkets | API key session | Back + Lay | BUILT |
| SX Bet | API key session | Buy + Sell | BUILT |
| Matchbook | Username/password | Back + Lay (0% commission) | BUILT |
| Gemini Predictions | HMAC-SHA384 | Buy + Sell (1%/5%) | BUILT |
| IBKR ForecastEx | TWS API | BUY-only, LMT, $0 | BUILT |
| Metaculus | Public REST | Read-only signal | BUILT |
| Manifold Markets | Public REST | Read-only signal | BUILT |

## Success Criteria

1. Net positive P&L in trades.db over 7-day period
2. <5% false positive rate on detected opportunities
3. At least one profitable autonomous round-trip trade

## Key Decisions (v1.0)

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Dual-layer fee routing (scan hint + executor re-validate) | Prevent stale fee paths from scan time | Good — falls back to default when stale |
| Module-level circuit breakers (not per-instance) | Shared state across all callers | Good — prevents cascade failures |
| Minute-bucket idempotency keys | Same order within 60s = same key | Good — matches DB dedup window |
| Priority queue with negated weights | Min-heap semantics for max-priority | Good — time-sensitive opps execute first |
| Attribute access for fee reload (`config.X`) | Avoids stale `from config import` binding | Good — reload propagates correctly |
| API error tolerance at revalidation (>= 2% ROI) | Prevents transient API errors from killing all trades | Pending — needs production validation |

## Architecture

Three layers + orchestration shell:

- **Orchestration**: `scanner.py` (facade) → `cli.py` (entry), `continuous.py` (loop), `display.py` (output)
- **Scan Layer**: `scans/` package — two-stage pattern (mid-price → CLOB refinement)
- **Execution Layer**: `executor.py` → platform traders → `risk_manager.py` → `db.py` (SQLite WAL)
- **Platform APIs**: 10 `*_api.py` modules with auth, retries, circuit breakers, proxy support
- **Monitoring**: `dashboard.py` + `dashboard_ui.py`, `metrics.py`, `alerting.py`, `notifier.py`

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-01 after v2.0 milestone start*
