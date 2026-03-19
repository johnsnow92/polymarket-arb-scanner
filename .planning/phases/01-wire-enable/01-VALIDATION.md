---
phase: 1
slug: wire-enable
status: draft
nyquist_compliant: false
wave_0_complete: false
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

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | INTEG-01 | integration | `pytest tests/ -k "fee_path or lowest_fee" -v` | ❌ W0 | ⬜ pending |
| 01-01-02 | 01 | 1 | INTEG-02 | unit | `pytest tests/ -k "market_maker or mm" -v` | ✅ | ⬜ pending |
| 01-01-03 | 01 | 1 | INTEG-03 | unit | `pytest tests/ -k "resolution" -v` | ✅ | ⬜ pending |
| 01-01-04 | 01 | 1 | INTEG-04 | unit | `pytest tests/ -k "bankroll or position_sizer" -v` | ❌ W0 | ⬜ pending |
| 01-01-05 | 01 | 1 | INTEG-05 | manual | N/A — documentation only | N/A | ⬜ pending |
| 01-02-01 | 02 | 1 | ENABLE-01 | unit | `pytest tests/ -k "market_maker" -v` | ✅ | ⬜ pending |
| 01-02-02 | 02 | 1 | ENABLE-02 | unit | `pytest tests/ -k "snapshot" -v` | ✅ | ⬜ pending |
| 01-02-03 | 02 | 1 | ENABLE-03 | unit | `pytest tests/ -k "gas_monitor" -v` | ✅ | ⬜ pending |
| 01-02-04 | 02 | 1 | ENABLE-04 | unit | `pytest tests/ -k "event_monitor" -v` | ✅ | ⬜ pending |
| 01-02-05 | 02 | 1 | ENABLE-05 | manual | N/A — Railway env var config | N/A | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- Existing infrastructure covers most phase requirements.
- New tests may be needed for `find_lowest_fee_path()` integration and `update_bankroll()` wiring — these will be created as part of implementation tasks.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Stale scan documentation | INTEG-05 | Documentation-only change | Verify CLAUDE.md contains stale scan one-shot behavior docs |
| Railway env vars configured | ENABLE-05 | External service config | Check Railway dashboard for all 8 platform credential env vars |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
