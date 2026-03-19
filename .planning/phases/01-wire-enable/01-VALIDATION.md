---
phase: 1
slug: wire-enable
status: approved
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-19
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | none — tests run directly |
| **Quick run command** | `pytest tests/ -x -q` |
| **Full suite command** | `pytest tests/ -v` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/ -x -q`
- **After every plan wave:** Run `pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | Wave 0 | Status |
|---------|------|------|-------------|-----------|-------------------|--------|--------|
| 01-01-T1 | 01 | 1 | INTEG-01 (scan) | unit (tdd=true) | `pytest tests/test_cross.py -x -k "fee_path or FeePath" -v` | tdd task creates tests | pending |
| 01-01-T2 | 01 | 1 | INTEG-01 (exec) | unit (tdd=true) | `pytest tests/test_executor.py -x -k "fee_path or FeePath" -v` | tdd task creates tests | pending |
| 01-02-T1a | 02 | 1 | INTEG-02 | unit (tdd=true) | `pytest tests/test_cli.py -x -k "market_maker" -v` | tdd task creates tests | pending |
| 01-02-T1b | 02 | 1 | INTEG-03 | unit (tdd=true) | `pytest tests/test_continuous.py -x -k "kalshi_resolution or KalshiResolution" -v` | tdd task creates tests | pending |
| 01-02-T2 | 02 | 1 | INTEG-04 | unit (tdd=true) | `pytest tests/test_continuous.py -x -k "bankroll or BankrollRefresh" -v` | tdd task creates tests | pending |
| 01-03-T1 | 03 | 1 | ENABLE-01..04 | config verify | `python -c "from config import MM_MIN_SPREAD, MM_MAX_INVENTORY; assert ..."` | N/A (config only) | pending |
| 01-03-T2 | 03 | 1 | INTEG-05 | doc verify | `python -c "content = open('CLAUDE.md').read(); assert 'stale' in ..."` | N/A (docs only) | pending |
| 01-03-T3 | 03 | 1 | ENABLE-05 | checkpoint | checkpoint:human-action (Railway config) | N/A (manual) | pending |

*Status: pending / green / red / flaky*

---

## Wave 0 Strategy

Wave 0 test gaps are satisfied by `tdd="true"` on implementation tasks. Each tdd task writes tests FIRST (RED phase), then implements (GREEN phase). This means:

- Plan 01-01 Tasks 1-2: Tests created as part of TDD RED phase before implementation
- Plan 01-02 Tasks 1-2: Tests created as part of TDD RED phase before implementation
- Plan 01-03: No tests needed (config defaults + documentation + checkpoint)

No separate Wave 0 plan is required because all code-producing tasks use TDD workflow.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Stale scan documentation | INTEG-05 | Documentation-only change | Verify CLAUDE.md contains stale scan one-shot behavior docs |
| Railway env vars configured | ENABLE-05 | External service config | checkpoint:human-action in Plan 01-03 Task 3 blocks completion |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or tdd="true" creates tests before implementation
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covered by tdd="true" tasks (tests written in RED phase before implementation)
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved
