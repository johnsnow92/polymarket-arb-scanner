---
phase: 2
slug: harden-test
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-20
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x |
| **Config file** | none — tests use inline fixtures |
| **Quick run command** | `pytest tests/ -x -q --timeout=30` |
| **Full suite command** | `pytest tests/ -v` |
| **Estimated runtime** | ~47 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/ -x -q --timeout=30`
- **After every plan wave:** Run `pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 1 | HARDEN-03 | unit | `pytest tests/test_decision_log.py -v` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | HARDEN-04 | unit | `pytest tests/test_rate_limiter.py -v` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 1 | HARDEN-05 | unit | `pytest tests/test_idempotency.py -v` | ❌ W0 | ⬜ pending |
| 02-02-02 | 02 | 1 | HARDEN-02 | unit | `pytest tests/test_fees.py -v` | ✅ | ⬜ pending |
| 02-03-01 | 03 | 2 | HARDEN-01 | integration | `pytest tests/integration/ -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_decision_log.py` — stubs for HARDEN-03 decision logging
- [ ] `tests/test_rate_limiter.py` — stubs for HARDEN-04 rate limiter + circuit breaker
- [ ] `tests/test_idempotency.py` — stubs for HARDEN-05 idempotent order placement
- [ ] `tests/integration/` — directory for HARDEN-01 integration test scripts

*Existing infrastructure covers HARDEN-02 (test_fees.py exists).*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Zero 429 errors in 1-hour run | HARDEN-04 | Requires live API credentials and sustained traffic | Run `python scanner.py --continuous --interval 30` for 1 hour, grep logs for 429 |
| Fee calculations match actual charges within 0.1% | HARDEN-02 | Requires live platform fee data comparison | Execute 3-5 dry-run trades per platform, compare logged fees vs platform docs |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
