# Polymarket Arb Scanner — Full Project Audit

## What This Project Is

A Python CLI tool that scans for arbitrage opportunities across 8 prediction market exchanges + 1 signal source. It detects price mispricings, validates them against real order books, and optionally executes trades automatically.

**Platforms (8 trading + 1 signal):** Polymarket, Kalshi, Betfair, Smarkets, SX Bet, Matchbook, Gemini Predictions, IBKR ForecastEx, Metaculus (read-only)

**Strategies (7 types):**
- Binary/NegRisk internal — same-platform overround arbs
- Back-all/Back-lay — exchange-specific arbs (Betfair, Smarkets, SX Bet, Matchbook)
- Cross-platform — 2-way mispricings between any pair (28 pairs)
- Multi-cross — multi-outcome cross-platform (NEW, partially wired)
- Triangular — 3-way mispricings across 3+ platforms
- Event divergence — Metaculus consensus vs platform price signals
- Spread — Polymarket/Kalshi bid-ask spreads

## How It Works

**Two modes:**
1. **One-shot** (`python scanner.py`): Parallel fetch → parallel scan → CLOB refinement → sort by capital efficiency → display/execute
2. **Continuous** (`python scanner.py --continuous`): Asyncio loop + WebSocket feeds for real-time price updates, periodic re-scans, settlement checks

**Two-stage detection** (all scan modules): Mid-price scan (fast, REST API) → CLOB refinement (accurate, actual ask prices). Candidates that aren't profitable at real ask prices get dropped.

**Execution pipeline** (`executor.py`): Risk check → Gas monitor gate → Price revalidation → Dynamic sizing → Order placement → Fill confirmation → DB logging → Hedging (if partial fill)

## How It's Configured

- **90+ env vars** defined in `config.py` with sensible defaults, validated at import time
- **CLI args** override env vars: `--mode`, `--continuous`, `--dry-run`, `--exec-mode`, `--min-profit`, etc.
- `DRY_RUN=true` by default (detect-only, no trades)
- Platform credentials: all via env vars, no hardcoded secrets (except one gap below)

## What It Runs On

- **Railway** auto-deploys on push to `master` via Dockerfile (`python:3.12-slim`)
- Health check: `/healthz` on configured port
- Docker entrypoint: `python scanner.py --continuous`
- SQLite (`trades.db`) for persistence with WAL mode
- CI: GitHub Actions runs `pytest` on PRs to `master` (315 pre-existing non-passing baseline)

## Architecture (4 layers)

| Layer | Files | Role |
|-------|-------|------|
| **Orchestration** | `scanner.py` (facade), `cli.py`, `continuous.py`, `display.py` | Entry points, arg parsing, dispatch |
| **Scan** | `scans/*.py` (15 modules) | Market detection, two-stage refinement |
| **Execution** | `executor.py`, `risk_manager.py`, `hedger.py`, `db.py` | Trading, risk, hedging, persistence |
| **Platform API** | `*_api.py` (9 clients), `ws_feeds.py` | Auth, REST, WebSocket, retries |

**Supporting:** `fees.py`, `matcher.py`, `gas_monitor.py`, `event_monitor.py`, `recovery.py`, `notifier.py`, `alerting.py`, `metrics.py`, `snapshot.py`, `backtest.py`, `dashboard.py`

---

# Audit Findings — Gaps & Issues

## CRITICAL (5 items)

### 1. MultiCross scan cannot execute trades
- `scans/multi_cross.py` exists and generates `MultiCross(n)` opportunities
- `cli.py` wires it in (`--mode multi-cross` works)
- **BUT** `executor.py:_build_legs()` has no branch for `MultiCross(n)` — returns empty legs, silently skips
- `executor.py` revalidation dispatch also missing MultiCross
- `continuous.py` doesn't wire in `scan_multi_cross` at all
- **No tests exist** (`test_multi_cross.py` missing)
- `scans/multi_cross.py` is **untracked** (not committed to git)
- **Impact:** User runs `--mode multi-cross`, sees opportunities, but none execute

### 2. Missing `ib_insync` in requirements.txt
- `ibkr_api.py` imports `ib_insync` but it's not in requirements.txt
- Docker image won't include it — IBKR scans always disabled in production
- **Fix:** Add `ib_insync>=1.0.0` to requirements.txt

### 3. SX Bet order signing not implemented
- `sxbet_api.py:place_order()` sends unsigned JSON
- Real SX Bet API requires Ethereum wallet signature (EIP-191/EIP-712)
- Orders will be silently rejected
- `get_balance()` also returns None (requires UUID API key, not wallet address)
- **Impact:** SX Bet is effectively read-only despite having trading code

### 4. Hardcoded password in run_dashboard.py
- `DASHBOARD_PASS = "Oaklandu15"` in plaintext source code
- Should use env var only
- **Fix:** Remove hardcoded default, use `os.getenv("DASHBOARD_PASS", "")`

### 5. No .env.example file
- 90+ env vars with no template or documentation outside config.py
- New contributors (or future-you) must reverse-engineer required vars
- **Fix:** Create `.env.example` with all vars and brief descriptions

## HIGH (6 items)

### 6. Betfair & Smarkets missing retry logic
- No `tenacity` retry decorators on `_request()` methods
- Network hiccups cause immediate failure (unlike Polymarket/Kalshi which retry 3x)
- **Fix:** Add `@retry()` decorators matching Polymarket/Kalshi pattern

### 7. Gemini 429 rate limit handling incorrect
- Converts 429 to generic `ConnectionError` for retry, losing rate limit context
- May exhaust retries without proper backoff
- **Fix:** Use explicit `_RateLimitError` pattern like Polymarket/Kalshi

### 8. IBKR connection has no timeout
- `ib.connect()` with no explicit timeout — hangs indefinitely if IB Gateway is down
- **Impact:** Continuous mode hangs at startup if IBKR configured but Gateway offline

### 9. Log files and workspace file not in .gitignore
- `dashboard_error.log`, `dashboard_output.log`, `polymarket-arb-scanner.code-workspace` show in git status
- **Fix:** Add to `.gitignore`

### 10. Deleted deployment infra not documented
- `.github/workflows/deploy.yml` and `infra/` folder deleted but migration to Railway-only not documented
- Unclear if this was intentional

### 11. No proxy support for 5 of 7 trading platforms
- Only Polymarket and Kalshi have proxy config
- Betfair, Smarkets, SX Bet, Matchbook, Gemini lack proxy support
- **Impact:** Cannot route these through corporate/residential proxies

## MEDIUM (5 items)

### 12. No dedicated Kalshi scan tests
- `test_kalshi_api.py` tests the API client, but no `test_kalshi_scan.py` for scan logic

### 13. No WebSocket feed tests
- `ws_feeds.py` is 950+ lines with no unit tests

### 14. Backtest engine incomplete
- `backtest.py` truncated/incomplete at ~80 lines

### 15. Metaculus cache TTL not configurable
- Hardcoded 5-minute cache, no env var

### 16. Recovery.py Gemini status parsing may be incomplete
- Likely defaults to "unknown" for Gemini order statuses

## LOW (3 items)

### 17. Rate limit sleeps not logged
- All clients enforce manual rate limits but don't log when sleeping

### 18. Matchbook event category hardcoded to "politics"
- Non-politics markets invisible

### 19. Dashboard auth logic undocumented
- `DASHBOARD_PASS=""` disables auth implicitly

---

# Platform Trading Readiness

| Platform | Status | Notes |
|----------|--------|-------|
| Polymarket | **FULL** | Production-ready, CLOB support, retries, proxy |
| Kalshi | **FULL** | Production-ready, RSA auth, retries, proxy |
| Gemini | **FULL** (minor issues) | 429 handling needs fix, no proxy |
| Matchbook | **FULL** | Session auth, 0% commission |
| Betfair | **Risky** | No retry logic |
| Smarkets | **Risky** | No retry logic |
| IBKR | **BUY-only** | By design, hedger correctly excludes |
| SX Bet | **READ-ONLY** | Order signing unimplemented |
| Metaculus | **Signal only** | As intended |

---

# Recommended Action Plan

## Phase 1: Critical fixes (immediate)
1. Add MultiCross `_build_legs()` + revalidation branches to `executor.py`
2. Wire `scan_multi_cross` into `continuous.py`
3. Create `test_multi_cross.py`
4. Commit `scans/multi_cross.py` to git
5. Add `ib_insync>=1.0.0` to `requirements.txt`
6. Remove hardcoded password from `run_dashboard.py`
7. Create `.env.example`
8. Add log files to `.gitignore`

## Phase 2: Reliability (high priority)
9. Add retry logic to Betfair & Smarkets API clients
10. Fix Gemini 429 handling (`_RateLimitError`)
11. Add IBKR connection timeout
12. Add proxy support to remaining platforms

## Phase 3: Test coverage
13. Create `test_kalshi_scan.py`
14. Create WebSocket feed tests
15. Add MultiCross branches to `test_executor.py`

## Phase 4: Polish
16. Complete backtest engine
17. Implement SX Bet order signing (or document as read-only)
18. Make Metaculus cache TTL configurable
19. Document Railway-only deployment decision

## Verification
```bash
# After Phase 1 changes:
pytest tests/test_multi_cross.py -v          # New tests pass
pytest tests/test_cross.py -v                # Existing pass (43/43)
pytest tests/ -v --tb=no -q                  # Full suite ≤ 315 non-passing
python scanner.py --mode multi-cross --dry-run  # Scan works end-to-end
```
