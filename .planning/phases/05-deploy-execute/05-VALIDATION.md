---
phase: 05
slug: deploy-execute
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-01
---

# Phase 05 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 |
| **Config file** | none (default) |
| **Quick run command** | `pytest tests/test_fees.py tests/test_executor.py -v -x` |
| **Full suite command** | `pytest tests/ -v` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_fees.py tests/test_executor.py -v -x`
- **After every plan wave:** Run `pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 05-01-01 | 01 | 1 | EXEC-03 | unit | `python -c "from config import STRATEGY_LAYERS, REVAL_FLOORS, get_layer"` | ❌ W0 | ⬜ pending |
| 05-01-02 | 01 | 1 | EXEC-04 | unit | `pytest tests/test_fees.py -v -x` | ✅ (needs rewrite) | ⬜ pending |
| 05-01-03 | 01 | 1 | EXEC-03 | unit | `python -c "from backtest import STRATEGY_LAYERS; from config import STRATEGY_LAYERS as SL2; assert STRATEGY_LAYERS is SL2"` | ❌ W0 | ⬜ pending |
| 05-02-01 | 02 | 2 | EXEC-03 | unit | `python -c "import ast,sys,os; [exit(1) for f in os.listdir('scans') if f.endswith('.py') and f not in ('__init__.py','helpers.py') and '\"_layer\"' not in open(os.path.join('scans',f)).read() and '\"type\"' in open(os.path.join('scans',f)).read()]"` | ❌ W0 | ⬜ pending |
| 05-02-02 | 02 | 2 | EXEC-01, EXEC-02, EXEC-03 | unit | `pytest tests/test_executor.py -v -x -k "layer or maker or calibration or reval"` | ❌ W0 | ⬜ pending |
| 05-03-01 | 03 | 3 | EXEC-01 | integration | `python -c "from config import MAX_TRADE_SIZE, DAILY_LOSS_LIMIT; assert MAX_TRADE_SIZE == 5.0; assert DAILY_LOSS_LIMIT == 25.0"` | ✅ | ⬜ pending |
| 05-03-02 | 03 | 3 | EXEC-01 | manual | Railway logs: search "REVAL|layer=" — verify all 4 layers appear | N/A | ⬜ pending |
| 05-03-03 | 03 | 3 | EXEC-07 | manual | `python -c "import sqlite3; c=sqlite3.connect('trades.db'); print(c.execute('SELECT count(*) FROM trades').fetchone())"` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_fees.py` — rewrite Polymarket tests for `P*(1-P)*rate` formula
- [ ] `tests/test_fees.py` — rewrite Gemini tests for new formula and rates
- [ ] `tests/test_fees.py` — add `kalshi_maker_fee` tests
- [ ] `tests/test_fees.py` — add PLATFORM_FEE_SCHEDULE assertions for all 8 platforms (D-06)
- [ ] `tests/test_executor.py` — add layer-aware revalidation floor tests
- [ ] `tests/test_executor.py` — add maker routing tests (timeout, cancel behavior)
- [ ] `tests/test_executor.py` — add calibration logging format tests
- [ ] Config validation for `REVAL_FLOOR_L*` env vars

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| 72h dry-run pass rate 5-30% | EXEC-01 | Requires production Railway logs over 72h | Search Railway logs for "REVAL\|layer=", count passed=True vs total |
| First profitable round-trip trade | EXEC-07 | Requires live platform credentials and market conditions | Monitor trades.db via /status endpoint for P&L > 0 |
| Railway deployment health | EXEC-01 | Requires live infrastructure | Check /healthz returns 200 |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
