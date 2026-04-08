---
phase: 08
slug: market-signal-strategies
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-04
---

# Phase 08 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | none — existing pytest setup |
| **Quick run command** | `pytest tests/test_imbalance.py tests/test_news_snipe.py tests/test_correlated.py tests/test_time_decay.py -v` |
| **Full suite command** | `pytest tests/ -v` |
| **Estimated runtime** | ~45 seconds |

---

## Sampling Rate

- **After every task commit:** Run quick command for the strategy being modified
- **After every plan wave:** Run full suite command
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 45 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 08-01-01 | 01 | 1 | STRAT-01 | — | N/A | unit | `pytest tests/test_imbalance.py -v` | ❌ W0 | ⬜ pending |
| 08-02-01 | 02 | 1 | STRAT-02 | T-08-01 | News API key not logged | unit | `pytest tests/test_news_snipe.py -v` | ❌ W0 | ⬜ pending |
| 08-03-01 | 03 | 2 | STRAT-06 | — | N/A | unit | `pytest tests/test_correlated.py -v` | ❌ W0 | ⬜ pending |
| 08-04-01 | 04 | 2 | STRAT-07 | — | N/A | unit | `pytest tests/test_time_decay.py -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_imbalance.py` — stubs for STRAT-01
- [ ] `tests/test_news_snipe.py` — stubs for STRAT-02
- [ ] `tests/test_correlated.py` — stubs for STRAT-06
- [ ] `tests/test_time_decay.py` — stubs for STRAT-07
- [ ] `finnhub-python` package added to requirements.txt

*Existing infrastructure covers test framework, DB fixtures, and mock patterns.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live Finnhub WebSocket latency | STRAT-02 | Requires live API key and network | Connect to Finnhub WS, measure end-to-end news-to-signal latency |
| Correlated pair accuracy | STRAT-06 | Requires live market data | Verify manual pair mappings produce correct divergence signals |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 45s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
