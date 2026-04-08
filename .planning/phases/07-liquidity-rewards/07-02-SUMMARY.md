---
phase: 07-liquidity-rewards
plan: 02
type: execute
subsystem: Detection
tags: [rewards, polymarket, kalshi, market-making]
dependencies:
  requires: [07-01]
  provides: [reward-detection, optimal-spread-calculation]
  affects: [continuous.py, executor.py, cli.py]
tech_stack:
  added: [scans/rewards.py, net_profit_rewards function]
  patterns: [two-stage scan, fee calculation]
key_files:
  created:
    - scans/rewards.py (350 lines)
  modified:
    - fees.py (net_profit_rewards function added)
    - scans/__init__.py (exports for scan functions)
decisions:
  - Polymarket rewards scan filters by pool_size >= $10 to avoid tiny pools
  - Target spread = max_incentive_spread * 0.6 for good reward score without excessive competition
  - Kalshi scan uses 3% of mid-price as target spread (conservative)
  - Kalshi opportunities generated for markets with >$1000 daily volume
  - Single-sided orders allowed only when midpoint in [0.10, 0.90] per Polymarket rules
metrics:
  duration: 15 minutes
  completed_date: 2026-04-04
  tasks_completed: 2
  commits: 2
---

# Phase 7 Plan 2: Rewards Scan Module - Summary

**One-liner:** Two-stage reward opportunity detection for Polymarket and Kalshi with optimal spread calculation and CLOB depth validation.

## Execution Report

### Tasks Completed

#### Task 1: Create scans/rewards.py with Polymarket and Kalshi reward opportunity detection
**Status:** COMPLETE

**Deliverables:**
- `scans/rewards.py` (350 lines) with complete two-stage scan pattern
- `scan_polymarket_rewards()` — detects reward-eligible Polymarket markets
  - Fetches markets with active `incentives` metadata from Markets API
  - Validates all fields: `min_incentive_size > 0`, `0 < max_incentive_spread <= 0.50`, `pool_size_usdc >= 0`
  - Filters by minimum pool size ($10 default)
  - Calculates optimal reward-aware bid/ask via helper function
  - Returns opportunities with type='PolymarketRewards', _layer=3, market_key, optimal_bid/ask, reward_pool_usdc
- `_refine_rewards_with_clob()` — Stage 2 refinement
  - Fetches live CLOB order book prices in parallel
  - Validates optimal quotes don't cross market
  - Checks minimum $5 depth on both sides
  - Drops opportunities with insufficient depth (graceful degradation if CLOB unavailable)
- `scan_kalshi_rewards()` — detects high-volume Kalshi markets
  - Fetches active markets via KalshiClient
  - Filters by daily volume > $1000
  - Generates resting limit order opportunities with calculated spreads
  - Returns opportunities with type='KalshiRewards', _layer=3, ticker, optimal_bid/ask
- Helper functions:
  - `_validate_reward_metadata()` — validates all required fields are present and sane
  - `_calculate_optimal_quotes()` — calculates bid/ask spread optimized for reward qualification

**Implementation Details:**
- Two-stage pattern follows existing scan architecture (binary.py, etc.)
- Stage 1: Fast mid-price scan with reward metadata validation
- Stage 2: Accurate CLOB depth check with parallel fetching
- Reward metadata validation per threat model T-07-05
- Conservative defaults: $10 minimum pool, 3% spread for Kalshi
- Polymarket double-sided requirement enforced for midpoints outside [0.10, 0.90]
- All candidate filtering logged with reason counts

**Verification:**
```
OK: Scan functions imported successfully
grep -n "def scan_polymarket_rewards|def scan_kalshi_rewards|def _refine_rewards_with_clob" scans/rewards.py
  76:def _refine_rewards_with_clob(...)
  167:def scan_polymarket_rewards(...)
  275:def scan_kalshi_rewards(...)
```

**Commit:** b6a8aa3 (feat(07-02): add rewards scan module for Polymarket and Kalshi)

---

#### Task 2: Add net_profit_rewards function to fees.py and export scan functions from scans/__init__.py
**Status:** COMPLETE

**Deliverables:**
- `net_profit_rewards()` function in fees.py (line 1074)
  - Calculates net profit for reward resting orders based on spread capture
  - Parameters: bid_price, ask_price, size (default 1.0), platform (default "polymarket")
  - Returns dict: net_profit, spread, fees, net_roi, bid, ask
  - Conservative fee rates: 0.5% for both Polymarket and Kalshi (makers typically 0% or lower)
  - Formula: net_profit = (ask - bid) * size - fees
- Exports from scans/__init__.py:
  - Added `from scans.rewards import scan_polymarket_rewards, scan_kalshi_rewards`
  - Added both functions to `__all__` list for public API

**Implementation Details:**
- Follows existing fee calculation patterns in fees.py
- Platform-specific fee handling (Polymarket, Kalshi)
- ROI calculation: (net_profit / size) * 100
- Conservative 0.5% fee estimate accounts for edge cases not fully known

**Verification:**
```
grep -n "def net_profit_rewards" fees.py
  1074:def net_profit_rewards(...)

from fees import net_profit_rewards
result = net_profit_rewards(0.30, 0.35, 10.0)
Result: {'net_profit': 0.4974999999999999, 'spread': 0.05, 'fees': 0.0025, ...}
net_profit > 0: True

from scans import scan_polymarket_rewards, scan_kalshi_rewards
OK: scan functions exported from scans package
```

**Commit:** e4c1317 (feat(07-02): add net_profit_rewards fee function and export scan functions)

---

## Compliance & Verification

### Plan Verification Checklist

- [x] scans/rewards.py exists with >200 lines (350 lines)
- [x] Contains scan_polymarket_rewards() function
- [x] Contains scan_kalshi_rewards() function
- [x] Contains _refine_rewards_with_clob() function
- [x] Functions return opportunities with type field set to 'PolymarketRewards' or 'KalshiRewards'
- [x] Polymarket scan filters by reward_pool_usdc >= 10
- [x] Polymarket scan validates max_incentive_spread between 0 and 0.50
- [x] Kalshi scan returns opportunities for resting orders
- [x] No import errors when importing scan functions
- [x] No live API calls in module (all passed as parameters)
- [x] fees.py contains net_profit_rewards() function
- [x] net_profit_rewards() accepts bid, ask, size, platform parameters
- [x] net_profit_rewards() returns dict with net_profit, spread, fees, net_roi keys
- [x] net_profit is positive when ask > bid
- [x] scans/__init__.py exports scan_polymarket_rewards and scan_kalshi_rewards
- [x] No import errors: `from fees import net_profit_rewards`
- [x] No import errors: `from scans import scan_polymarket_rewards, scan_kalshi_rewards`

### Threat Model Compliance

**T-07-05 (Tampering - Invalid reward metadata):** MITIGATED
- Implementation includes `_validate_reward_metadata()` function
- Validates all required fields present and within valid ranges
- Rejects markets with `max_incentive_spread > 0.50` or `min_size <= 0`
- Logs warnings for invalid data (consistent with plan)

**T-07-06 (Denial of Service - Scan exhaustion):** ACCEPTED
- Scan filtered by pool_size >= $10 (bounded by ~5k Polymarket markets)
- Kalshi scan filtered by daily volume > $1000 (bounded by active markets)

---

## Deviations from Plan

None — plan executed exactly as written.

---

## Key Patterns Established

### Two-Stage Scan Pattern (scans/rewards.py)

1. **Stage 1 (Fast mid-price scan):**
   - Fetch market data from API
   - Validate metadata (rewards metadata)
   - Calculate opportunity metrics (optimal spreads)
   - Filter by size/pool thresholds
   - Create opportunity dicts

2. **Stage 2 (Accurate CLOB refinement):**
   - Fetch live CLOB prices in parallel (ThreadPoolExecutor)
   - Validate achievability (quotes don't cross, depth sufficient)
   - Enrich opportunities with CLOB details (_clob_depth, _clob_refined)
   - Drop candidates that fail validation
   - Return refined list

This pattern is reused from binary.py, negrisk.py, and all other scan modules.

### Fee Calculation for Resting Orders (fees.py)

For reward orders, profit comes from spread capture (maker fees typically 0% or lower):
```python
spread = ask_price - bid_price
net_profit = spread * size - (conservative_fee_estimate * spread * size)
net_roi = (net_profit / size) * 100
```

Conservative fee estimate (0.5%) accounts for unknown fee edge cases.

---

## Downstream Integration Points

The following modules will consume these new scan functions and fee function in Phase 3:

- **cli.py** — Add `--mode rewards` argument for one-shot reward scans
- **continuous.py** — Integrate rewards scan into main continuous loop
- **executor.py** — Add `_build_legs("PolymarketRewards")` and `_build_legs("KalshiRewards")` branches
- **config.py** — Add REWARDS_* config variables (REWARDS_ENABLED, REWARDS_MAX_EXPOSURE, etc.)
- **market_maker.py** — Already has RewardTracker and KalshiRewardTracker classes (from Phase 1)
- **db.py** — Track reward metrics (already has log_reward_metric method from Phase 1)

---

## Known Limitations & Future Work

1. **Polymarket reward API polling not yet implemented** — Phase 3 will add periodic polling of Markets API for reward score updates
2. **Dashboard rewards metrics not yet added** — Phase 3 will add reward section to dashboard_ui.py and /status endpoint
3. **Continuous mode reward loop not yet wired** — Phase 3 will integrate into run_continuous()
4. **Execution not yet implemented** — Phase 3 will handle order placement and fill tracking

These are expected gaps; the plan scope was limited to detection + fee calculation for Phase 2.

---

## Test Status

No new tests created in this phase (unit tests for reward scanning will be in Phase 3).
Existing test infrastructure (pytest, unittest.mock) is ready to support reward tests.

---

## Files Changed

**Created:**
- `/c/Users/jtamm/Dev/polymarket-arb-scanner/scans/rewards.py` (350 lines)

**Modified:**
- `/c/Users/jtamm/Dev/polymarket-arb-scanner/fees.py` (+51 lines)
- `/c/Users/jtamm/Dev/polymarket-arb-scanner/scans/__init__.py` (+3 lines)

**Total:** 404 lines added, 0 lines removed

---

## Commits

| Hash | Message |
|------|---------|
| b6a8aa3 | feat(07-02): add rewards scan module for Polymarket and Kalshi |
| e4c1317 | feat(07-02): add net_profit_rewards fee function and export scan functions |

---

## Self-Check

**Created files verification:**
- [x] scans/rewards.py exists
- [x] fees.py modified successfully
- [x] scans/__init__.py modified successfully

**Commits verification:**
```
b6a8aa3 - feat(07-02): add rewards scan module for Polymarket and Kalshi
e4c1317 - feat(07-02): add net_profit_rewards fee function and export scan functions
```

**All checks passed.**
