---
phase: 3
slug: monitor-optimize
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-21
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x |
| **Config file** | none — tests use inline fixtures |
| **Quick run command** | `pytest tests/ -x -q --ignore=tests/integration` |
| **Full suite command** | `pytest tests/ -v --ignore=tests/integration` |
| **Estimated runtime** | ~70 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/ -x -q --ignore=tests/integration`
- **After every plan wave:** Run `pytest tests/ -v --ignore=tests/integration`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 90 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 1 | MONITOR-02 | unit | `pytest tests/test_metrics_wiring.py -v` | ❌ W0 | ⬜ pending |
| 03-01-02 | 01 | 1 | MONITOR-03 | unit | `pytest tests/test_anomaly_alerting.py -v` | ❌ W0 | ⬜ pending |
| 03-02-01 | 02 | 1 | MONITOR-01 | unit | `pytest tests/test_dashboard_endpoints.py -v` | ❌ W0 | ⬜ pending |
| 03-02-02 | 02 | 1 | OPTIMIZE-04 | unit | `pytest tests/test_bankroll_tracking.py -v` | ❌ W0 | ⬜ pending |
| 03-03-01 | 03 | 2 | OPTIMIZE-03 | unit | `pytest tests/test_priority_queue.py -v` | ❌ W0 | ⬜ pending |
| 03-03-02 | 03 | 2 | OPTIMIZE-01, OPTIMIZE-02 | unit | `pytest tests/test_fee_backtest.py -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_metrics_wiring.py` — stubs for MONITOR-02 per-strategy metrics
- [ ] `tests/test_anomaly_alerting.py` — stubs for MONITOR-03 loss spike detection
- [ ] `tests/test_dashboard_endpoints.py` — stubs for MONITOR-01 new API endpoints
- [ ] `tests/test_priority_queue.py` — stubs for OPTIMIZE-03 priority execution

*Existing test infrastructure covers framework needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Dashboard renders P&L charts | MONITOR-01 | Visual rendering in browser | Open `http://localhost:8080/` and verify chart renders |
| Rebalancing recommendation digest | MONITOR-04, OPTIMIZE-05 | Requires multi-platform balance state | Run bot for 1 week, verify weekly webhook fires |
| Priority queue meets 500ms target | OPTIMIZE-03 | Timing-sensitive, needs live conditions | Measure time from WS event to order placement in continuous mode |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 90s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
