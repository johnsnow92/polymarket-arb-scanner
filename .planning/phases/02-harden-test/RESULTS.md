# Phase 2: Harden & Test — Integration Test Results

**Run date:** 2026-03-21 04:05:08
**Total:** 19 tested, 1 passed, 0 failed, 18 skipped

## Strategy Dry-Run Tests (HARDEN-01)

| Strategy | Mode | Status | Evidence | Timestamp |
|----------|------|--------|----------|-----------|
| Back-all/Back-lay (Betfair) | betfair | SKIP | No Betfair credentials (BETFAIR_APP_KEY / BETFAIR_USERNAME / BETFAIR_PASSWORD) | 2026-03-21T04:05:03 |
| Binary internal arb | binary | SKIP | No Polymarket credentials (POLYMARKET_PRIVATE_KEY) | 2026-03-21T04:05:03 |
| Cross-platform convergence | convergence | SKIP | Requires Polymarket + Kalshi credentials | 2026-03-21T04:05:03 |
| Cross-all platform pairs | cross-all | SKIP | Requires Polymarket + Kalshi credentials (minimum pair) | 2026-03-21T04:05:03 |
| Cross-platform 2-way | cross | SKIP | Requires Polymarket + Kalshi credentials | 2026-03-21T04:05:03 |
| Event divergence (Metaculus) | event | PASS | Exit code 0, no Traceback in stderr | 2026-03-21T04:05:03 |
| Gemini binary + multi | gemini | SKIP | No Gemini credentials (GEMINI_API_KEY / GEMINI_API_SECRET) | 2026-03-21T04:05:08 |
| IBKR ForecastEx binary | ibkr | SKIP | No IBKR credentials (IBKR_HOST) | 2026-03-21T04:05:08 |
| Kalshi binary + multi | kalshi | SKIP | No Kalshi credentials (KALSHI_API_KEY_ID) | 2026-03-21T04:05:08 |
| Back-all/Back-lay (Matchbook) | matchbook | SKIP | No Matchbook credentials (MATCHBOOK_USERNAME / MATCHBOOK_PASSWORD) | 2026-03-21T04:05:08 |
| Market making (dry-run) | mm | SKIP | No Polymarket credentials (POLYMARKET_PRIVATE_KEY) | 2026-03-21T04:05:08 |
| Multi-outcome cross-platform | multi-cross | SKIP | Requires Polymarket + Kalshi credentials | 2026-03-21T04:05:08 |
| NegRisk internal arb | negrisk | SKIP | No Polymarket credentials (POLYMARKET_PRIVATE_KEY) | 2026-03-21T04:05:08 |
| Resolution sniping | resolution | SKIP | Requires Polymarket + Kalshi credentials | 2026-03-21T04:05:08 |
| Back-all/Back-lay (Smarkets) | smarkets | SKIP | No Smarkets credentials (SMARKETS_API_KEY) | 2026-03-21T04:05:08 |
| Bid-ask spread | spread | SKIP | Requires Polymarket + Kalshi credentials | 2026-03-21T04:05:08 |
| Stale price exploitation | stale | SKIP | No Polymarket credentials (POLYMARKET_PRIVATE_KEY) | 2026-03-21T04:05:08 |
| Back-all/Back-lay (SX Bet) | sxbet | SKIP | No SX Bet credentials (SXBET_API_KEY) | 2026-03-21T04:05:08 |
| Triangular 3-way arb | triangular | SKIP | Requires Polymarket + Kalshi credentials | 2026-03-21T04:05:08 |

## Fee Verification (HARDEN-02)

| Check | Status | Evidence | Timestamp |
|-------|--------|----------|-----------|
| Fee verification (all 8 platforms) | PASS | PASS (3 cases) IBKR : PASS (3 cases) Kalshi : PASS (3 cases) Matchbook : PASS (3 cases) Polymarket : PASS (3 cases)... | 2026-03-21T04:05:08 |
