---
phase: 07
slug: liquidity-rewards
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-04
---

# Phase 07 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | none — existing pytest setup |
| **Quick run command** | `pytest tests/test_rewards.py -v` |
| **Full suite command** | `pytest tests/ -v` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_rewards.py -v`
- **After every plan wave:** Run `pytest tests/ -v`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 07-01-01 | 01 | 1 | EXEC-05 | — | N/A | unit | `pytest tests/test_rewards.py -v` | ❌ W0 | ⬜ pending |
| 07-01-02 | 01 | 1 | EXEC-06 | — | N/A | unit | `pytest tests/test_rewards.py -v` | ❌ W0 | ⬜ pending |
| 07-02-01 | 02 | 1 | STRAT-03 | — | N/A | unit | `pytest tests/test_rewards.py -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_rewards.py` — stubs for EXEC-05, EXEC-06, STRAT-03
- [ ] Reward API response fixtures for Polymarket and Kalshi

*Existing infrastructure covers test framework and DB fixtures.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live reward score tracking | EXEC-05 | Requires live Polymarket account with resting orders | Place test order, verify reward score appears in dashboard after epoch |
| Kalshi incentive qualification | EXEC-06 | Requires live Kalshi account with qualifying orders | Place resting order, verify qualification metrics logged |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
