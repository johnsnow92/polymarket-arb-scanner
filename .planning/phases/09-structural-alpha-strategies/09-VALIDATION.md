---
phase: 09
slug: structural-alpha-strategies
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-05
---

# Phase 09 — Validation Strategy

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Quick run command** | `pytest tests/test_logical_arb.py tests/test_whale_copy.py -v` |
| **Full suite command** | `pytest tests/ -v` |
| **Estimated runtime** | ~45 seconds |

## Sampling Rate

- **After every task commit:** Run quick command for the strategy being modified
- **After every plan wave:** Run full suite command
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 45 seconds

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 09-01-01 | 01 | 1 | STRAT-04 | unit | `pytest tests/test_logical_arb.py -v` | No W0 | pending |
| 09-02-01 | 02 | 1 | STRAT-05 | unit | `pytest tests/test_whale_copy.py -v` | No W0 | pending |

## Wave 0 Requirements

- [ ] `tests/test_logical_arb.py` — stubs for STRAT-04
- [ ] `tests/test_whale_copy.py` — stubs for STRAT-05

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live Polygonscan API latency | STRAT-05 | Requires live API key | Monitor end-to-end wallet event to order placement latency |
| Semantic rule accuracy | STRAT-04 | Requires live market data | Verify rules produce correct implied relationships |

## Validation Sign-Off

- [ ] All tasks have automated verify or Wave 0 dependencies
- [ ] Feedback latency < 45s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
