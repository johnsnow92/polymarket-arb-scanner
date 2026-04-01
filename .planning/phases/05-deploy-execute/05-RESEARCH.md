# Phase 5: Deploy & Execute - Research

**Researched:** 2026-04-01
**Domain:** Production execution pipeline -- revalidation calibration, maker routing, fee verification, first live trade
**Confidence:** HIGH

## Summary

Phase 5 requires four workstreams: (1) layer-aware revalidation with calibration logging, (2) maker/limit order routing on Polymarket and Kalshi, (3) fee audit against current 2026 platform schedules, and (4) enabling live execution for the first profitable round-trip trade.

**Critical discovery: Polymarket's fee model has fundamentally changed.** The codebase uses a `polymarket_fee()` function that calculates `0.02 * (sell_price - buy_price)` (2% on net winnings). As of March 2026, Polymarket Global uses a dynamic taker fee formula: `feeRate * C * P * (1 - P)`, with category-specific rates ranging from 0.04 (politics) to 0.072 (crypto). Geopolitical/world events remain fee-free. Makers pay zero fees and receive rebates. This is not a minor rate change -- it is a completely different fee model that affects every Polymarket fee calculation in `fees.py`.

**Gemini Predictions also changed.** As of March 18, 2026, the formula changed from `min(P, 1-P) * fee_rate` to `fee_rate * C * P * (1 - P)`, with taker rate increased from 0.05 to 0.07 and maker rate set at 0.0175. The codebase's `gemini_fee()` function uses the old formula.

**Primary recommendation:** Fix fee calculations first (they affect revalidation accuracy), then implement layer tagging and revalidation floors, then maker routing, then deploy for 72h dry-run calibration.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Auto-tune revalidation floors from dry-run data. Deploy with DRY_RUN=true for 72 hours. Log every revalidation decision with: candidate ROI at scan time, ROI at revalidation, delta, rejection reason, time elapsed, and layer tag. Compute 80th-percentile price drift per layer after sufficient samples (~50+ per layer).
- **D-02:** Use roadmap values (2% L1, 5% L2, 3% L3, 10% L4) as initial floors while collecting calibration data. Replace with auto-tuned values after 72h observation period.
- **D-03:** Tag layer in opportunity dict -- each scan module sets `opp["_layer"]` = 1-4 based on strategy type. Executor reads `_layer` for floor lookup. Strategy-to-layer mapping per CLAUDE.md scope definition.
- **D-04:** 72-hour minimum dry-run calibration period before enabling live trading. Target pass rate: 5-30% (per PITFALLS.md).
- **D-05:** Route qualifying orders as limit (maker) on Polymarket and Kalshi. Cancel and skip unfilled orders after timeout -- no taker fallback.
- **D-06:** Manual audit of all 8 platform fee rates against current 2026 platform fee pages + automated pytest assertions codifying correct rates. CI catches future drift.
- **D-07:** Hardcoded fee rates in fees.py with env-var overrides (e.g., POLYMARKET_TAKER_FEE). Allows hotfixing a fee change via Railway env vars without deploy.
- **D-08:** All strategy layers eligible for first live trades simultaneously. $5 max trade size initially.
- **D-09:** $25/day daily loss limit during initial live trading period (5 losing trades at $5 before circuit breaker).
- **D-10:** Success = at least one round-trip trade with net positive P&L recorded in trades.db.

### Claude's Discretion
- Maker order aggressiveness per strategy layer (Claude decides based on urgency and layer type -- Layer 1 time-sensitive arbs more aggressive, Layer 3-4 more passive)
- Specific timeout duration for unfilled maker orders (5-10s range)

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| EXEC-01 | Bot deploys revalidation fix and validates with 24h dry-run showing 5-30% pass rate | Layer-aware floor lookup in `_get_revalidation_threshold()`, calibration logging infrastructure, 72h dry-run with per-layer stats |
| EXEC-02 | Executor routes orders as maker (limit) instead of taker (market) on Polymarket and Kalshi | Existing `ORDER_TIME_IN_FORCE` config + `_build_legs` GTC logic; extend to be layer-aware with configurable timeout |
| EXEC-03 | Revalidation thresholds are strategy-layer-aware (2% Layer 1, 5% Layer 2, 3% Layer 3, 10% Layer 4) | `backtest.py:_get_layer()` already maps opp_type to layer; propagate `_layer` tag into opp dicts at scan time, read in executor |
| EXEC-04 | Fee calculations verified against current 2026 platform fee structures for all 8 platforms | **CRITICAL:** Polymarket and Gemini fee formulas have changed fundamentally (see Fee Audit section); existing `verify_fees.py` scaffold needs complete rewrite for new formulas |
| EXEC-07 | Bot executes at least one profitable autonomous round-trip trade | After calibration period, flip DRY_RUN=false with MAX_TRADE_SIZE=5, DAILY_LOSS_LIMIT=25 |
</phase_requirements>

## Fee Audit: Current 2026 Platform Fee Schedules

### CRITICAL: Polymarket Fee Model Change

**Old model (in codebase):** `polymarket_fee(buy_price, sell_price) = 0.02 * (sell_price - buy_price)` -- 2% on net winnings at settlement.

**New model (Polymarket Global, March 2026):** Dynamic taker fee at trade time:
```
taker_fee = fee_rate * C * P * (1 - P)
```
Where C = contracts, P = price, fee_rate varies by category:

| Category | Fee Rate | Peak Effective Rate |
|----------|----------|---------------------|
| Crypto | 0.072 | 1.80% |
| Economics | 0.06 | 1.50% |
| Culture, Weather, Other, Mentions | 0.05 | 1.25% |
| Finance | 0.04 | 1.00% |
| Politics, Tech | 0.04 | 1.00% |
| Sports | 0.03 | 0.75% |
| Geopolitical/World Events | 0.00 | 0% (fee-free) |

- **Makers pay 0% fees** -- makers receive rebates (25% politics, 50% finance)
- **No settlement/winning fee** -- shares redeem at exactly $1.00
- Fees peak at P=0.50 and decrease toward extremes
- Fees collected in shares on buys, in USDC on sells

**Impact on codebase:** Every function that calls `polymarket_fee()` must be updated. The fee is now paid at trade entry (not settlement), varies by market category, and follows a completely different formula. This affects `net_profit_binary_internal`, `net_profit_cross_platform`, `net_profit_cross_*` (all Polymarket cross variants), `net_profit_triangular`, `net_profit_multi_cross`, `PLATFORM_FEE_SCHEDULE`, `estimate_total_fee`, and `_platform_win_fee`/`_platform_entry_fee`.

**Note on Polymarket US DCM:** Polymarket US (regulated DCM) uses a simpler model: 30 bps taker fee on total contract premium, 20 bps maker rebate. The bot likely interacts with the Global platform (crypto-based, using `POLYMARKET_PRIVATE_KEY`), not the US DCM.

### Gemini Predictions Fee Change (March 18, 2026)

**Old model (in codebase):** `gemini_fee(price, fee_rate) = min(P, 1-P) * fee_rate` with default 5% taker / 1% maker.

**New model:**
```
fee = fee_rate * C * P * (1 - P)
```
- Taker rate: 0.07 (was 0.05)
- Maker rate: 0.0175 (was 0.01)
- No settlement fees, no cancellation fees
- Fees rounded up to next cent

**Impact:** `gemini_fee()` formula must change from `min(P, 1-P) * rate` to `P * (1-P) * rate`. Default taker rate from 0.05 to 0.07, maker from 0.01 to 0.0175. Affects `net_profit_gemini_binary`, `net_profit_gemini_multi`, `net_profit_cross_gemini`.

### Kalshi Fees (Confirmed Unchanged)

**Taker:** `ceil(0.07 * C * P * (1 - P))` per contract in cents, min 2 cents, cap $1.75. **Confirmed current.**
**Maker:** `ceil(0.0175 * C * P * (1 - P))` per contract. **New finding: maker fee exists.** The codebase's `kalshi_taker_fee()` is correct for takers. A `kalshi_maker_fee()` function is needed for maker routing.
**Special:** S&P 500 / Nasdaq-100 markets use halved multiplier (0.035 taker).

### Betfair (Confirmed Unchanged)
- Base: 5% commission on net winnings (standard rate)
- Discount tiers: 2-5% based on My Betfair Rewards tier
- Expert/Premium charge: 20-60% for highly profitable accounts (>GBP 250K lifetime)
- Codebase default `BETFAIR_COMMISSION_RATE=0.03` is reasonable for moderate volume

### Smarkets (Confirmed Unchanged)
- Standard: 2% on net winnings
- Pro tier (1500+ bets/month or GBP 1M+ staked): 1%
- Select tier (GBP 25K+ net profit in 12 months): 3%
- Codebase default `SMARKETS_COMMISSION_RATE=0.02` is correct

### SX Bet (Confirmed Unchanged)
- Single bets: 0% commission
- Winning parlays: 5%, cross-chain bets: 3%
- Codebase correctly implements 0% for standard trades

### Matchbook (Needs Verification)
- Standard exchange: 1.5% commission (0.75% for maker side)
- Prediction markets: launched Jan 2026 with 0% promo for 110 days
- Post-promo rate for prediction markets: unclear, may differ from standard exchange rate
- Codebase assumes 0% -- may be correct for prediction markets during promotional period but needs monitoring

### IBKR ForecastEx (Confirmed Unchanged)
- $0.00 commission (fee built into spread: Yes + No = $1.01, 1 cent to exchange)
- Codebase correctly implements 0% commission
- Note: IBKR pays 3.14% APY interest coupon on positions

## Architecture Patterns

### Layer Tagging Pattern

The `_layer` tag follows established `_`-prefixed internal key convention. A mapping already exists in two places:

```python
# backtest.py STRATEGY_LAYERS dict (existing)
STRATEGY_LAYERS = {
    "Binary": 1, "KalshiBinary": 1, "Cross": 1, "NegRisk": 1,
    "MultiCross": 1, "TriangularCross": 1,
    "BetfairBackAll": 1, "BetfairBackLay": 1,
    "SmarketsBackAll": 1, "SmarketsBackLay": 1,
    "SXBetBackAll": 1, "SXBetBackLay": 1,
    "MatchbookBackAll": 1, "MatchbookBackLay": 1,
    "GeminiBinary": 1, "GeminiMulti": 1,
    "IBKRBinary": 1,
    "StalePriceOpp": 2, "ResolutionSnipeOpp": 2,
    "MarketMake": 3,
    "EventDivergence": 4, "ConvergenceOpp": 4,
}
```

**Recommendation:** Extract this mapping to a shared location (e.g., `config.py` or a new `layers.py`) so both `backtest.py`, `snapshot.py`, and `executor.py` use the same source of truth. Each scan module sets `opp["_layer"]` using this mapping.

### Revalidation Floor Lookup

Current `_get_revalidation_threshold()` uses ROI tiers (>5%, 2-5%, <2%) with a single `revalidation_min_floor`. Replace with layer-aware lookup:

```python
# config.py additions
REVAL_FLOOR_L1 = _env_float("REVAL_FLOOR_L1", "0.02")  # 2% Layer 1 pure arb
REVAL_FLOOR_L2 = _env_float("REVAL_FLOOR_L2", "0.05")  # 5% Layer 2 near-arb
REVAL_FLOOR_L3 = _env_float("REVAL_FLOOR_L3", "0.03")  # 3% Layer 3 market making
REVAL_FLOOR_L4 = _env_float("REVAL_FLOOR_L4", "0.10")  # 10% Layer 4 informed
REVAL_FLOORS = {1: REVAL_FLOOR_L1, 2: REVAL_FLOOR_L2, 3: REVAL_FLOOR_L3, 4: REVAL_FLOOR_L4}
```

The floor is used when adaptive revalidation kicks in for low-ROI opportunities (the `else` branch in `_get_revalidation_threshold`). Instead of `self.revalidation_min_floor`, look up `REVAL_FLOORS.get(opp.get("_layer", 1), REVAL_FLOOR_L1)`.

### Calibration Logging Pattern

Add structured logging to `_revalidate()` for every decision:

```python
# After revalidation result is determined
logger.info(
    "REVAL|layer=%d|type=%s|scan_roi=%.4f|reval_roi=%.4f|delta=%.4f|"
    "passed=%s|reason=%s|elapsed_ms=%d|floor=%.4f",
    layer, opp_type, scan_roi, reval_roi, delta,
    passed, reason, elapsed_ms, floor
)
```

Use a parseable format (pipe-delimited key=value) so a post-processing script can compute per-layer 80th-percentile drift from Railway logs.

### Maker Routing Pattern

The executor already has `ORDER_TIME_IN_FORCE` config and GTC logic in `_execute_kalshi_leg()`. Extend to make this layer-aware:

- **Layer 1 (time-sensitive arbs):** Aggressive limit -- place at best ask minus 1 tick, 5s timeout
- **Layer 2 (near-arb):** Moderate -- place at mid-price, 7s timeout
- **Layer 3-4 (MM/informed):** Passive -- place at target price, 10s timeout

For Polymarket: use `post_order` with `order_type="GTC"` instead of FOK. The CLOB API supports GTC natively. Cancel via `cancel_order` after timeout.

For Kalshi: already implemented -- `time_in_force="gtc"` parameter. Just need to make it the default for qualifying orders.

**No taker fallback per D-05.** If maker order times out, cancel and skip.

### Fee Model Refactor Pattern

The Polymarket fee change requires a structural refactor:

```python
# New polymarket_taker_fee() -- replaces polymarket_fee()
def polymarket_taker_fee(price: float, contracts: int = 1,
                         category: str = "politics") -> float:
    """Polymarket dynamic taker fee (March 2026 model).
    
    fee = fee_rate * C * P * (1 - P)
    Maker fee is always 0.
    """
    rate = POLYMARKET_FEE_RATES.get(category, 0.04)
    if price <= 0 or price >= 1:
        return 0.0
    return rate * contracts * price * (1 - price)

POLYMARKET_FEE_RATES = {
    "crypto": 0.072,
    "economics": 0.06,
    "culture": 0.05, "weather": 0.05, "other": 0.05, "mentions": 0.05,
    "finance": 0.04,
    "politics": 0.04, "tech": 0.04,
    "sports": 0.03,
    "geopolitical": 0.0, "world_events": 0.0,
}
```

Key difference: the old model charged fees at settlement (winning side only). The new model charges fees at trade time (every taker order). This means:
- Binary internal arb: each taker buy pays `fee_rate * P * (1-P)`, not 2% of winnings
- Cross-platform: Polymarket entry fee is now `polymarket_taker_fee(price)`, not a conditional win fee
- Makers on Polymarket pay 0% -- maker routing (D-05) now saves **all** Polymarket fees, not just a portion

### Market Category Detection

The fee depends on market category. Two approaches:
1. **API-based:** Polymarket API returns market metadata including category/tags. Attach `_category` to opportunity dicts during scan.
2. **Default with override:** Default to "politics" (0.04 rate), allow env-var `POLYMARKET_DEFAULT_FEE_RATE` override. Conservative approach for Phase 5.

**Recommendation:** Use approach 2 for Phase 5 (simpler, still correct for most markets). Add category detection in a future phase.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Fee formula validation | Manual spot-checks | Parameterized pytest with official formula reimplementation | 8 platforms x multiple price points = too many cases for manual review |
| Revalidation calibration analysis | Custom analysis script | Structured log parsing + numpy percentile | 80th-percentile computation is a one-liner with numpy; focus on logging format |
| Order timeout management | Custom timer threads | asyncio.wait_for or threading.Timer + cancel callback | Already used in continuous.py; executor needs consistent timeout pattern |
| Layer mapping maintenance | Duplicated dicts | Single source of truth in config or shared module | Already duplicated in backtest.py and snapshot.py; adding executor makes 3 copies |

## Common Pitfalls

### Pitfall 1: Wrong Fee Model Causes All Arbs to Appear Unprofitable
**What goes wrong:** After updating fees to the new Polymarket model, all binary internal arbs show negative net profit because the new entry-based fee is charged on both legs, not just the winning side.
**Why it happens:** Old model: one fee on winner at settlement. New model: two fees at entry (one per taker buy). For a binary arb buying YES at 0.45 and NO at 0.50, old fee = 0.02 * (1-0.45) = $0.011. New fee (politics, 0.04 rate) = 0.04 * 0.45 * 0.55 + 0.04 * 0.50 * 0.50 = $0.0099 + $0.01 = $0.0199. Similar magnitude but different distribution.
**How to avoid:** Recalculate expected net profits for known test cases after fee update. Verify that the pass rate in dry-run is 5-30%, not 0%.
**Warning signs:** Pass rate drops to 0% after fee update; all binary arbs show negative net profit.

### Pitfall 2: Maker Routing With No Taker Fallback Causes 0% Execution Rate
**What goes wrong:** All maker orders time out because the bot places at a price no one takes within the timeout window. Execution rate drops to 0%.
**Why it happens:** Prediction markets have thin order books. A limit order that's even 1 tick away from the current best may sit unfilled for minutes.
**How to avoid:** Place maker orders at the current best ask (for buys) or best bid (for sells) -- still qualifies as maker if it's a limit order. Aggressiveness should match Layer 1 urgency (arbs are time-sensitive). Monitor fill rates during dry-run.
**Warning signs:** Fill rate during dry-run logging period is consistently <5%.

### Pitfall 3: Layer Tag Not Set Causes All Opps to Use Default Floor
**What goes wrong:** Scan modules don't set `opp["_layer"]`, so executor uses a default floor for everything. Layer-specific calibration never takes effect.
**Why it happens:** Adding `_layer` to 15+ scan modules is easy to miss on one or two.
**How to avoid:** Add a guard in executor: if `_layer` not in opp, log a warning and derive it from opp_type using the existing `_get_layer()` mapping as fallback.

### Pitfall 4: Matchbook Fee Promo Expires Mid-Run
**What goes wrong:** Matchbook's 0% prediction market promo expires (110 days from account creation). Fees suddenly apply, turning previously profitable Matchbook arbs into losers.
**How to avoid:** Track promo expiry date. Add env-var `MATCHBOOK_COMMISSION_RATE` (default 0.0) that can be updated when promo expires.

## Code Examples

### Layer-Aware Revalidation Floor (executor.py modification)
```python
# Source: CONTEXT.md D-02, D-03
from config import REVAL_FLOORS

def _get_layer_floor(self, opp: dict) -> float:
    """Get revalidation floor for this opportunity's strategy layer."""
    layer = opp.get("_layer")
    if layer is None:
        # Fallback: derive from opp type
        opp_type = opp.get("type", "")
        layer = _get_layer_from_type(opp_type)
    return REVAL_FLOORS.get(layer, REVAL_FLOORS.get(1, 0.02))
```

### New Polymarket Fee Function
```python
# Source: docs.polymarket.com/trading/fees (April 2026)
# Replaces old polymarket_fee() which used 2% on net winnings

POLYMARKET_CATEGORY_RATES: dict[str, float] = {
    "crypto": 0.072, "economics": 0.06,
    "culture": 0.05, "weather": 0.05, "other": 0.05, "mentions": 0.05,
    "finance": 0.04, "politics": 0.04, "tech": 0.04,
    "sports": 0.03,
    "geopolitical": 0.0, "world_events": 0.0,
}

# Default rate, overridable via env var for hotfixing
_DEFAULT_PM_RATE = _env_float("POLYMARKET_TAKER_FEE_RATE", "0.04")

def polymarket_taker_fee(price: float, contracts: int = 1,
                         fee_rate: float | None = None) -> float:
    """Dynamic taker fee: fee_rate * C * P * (1 - P). Makers pay 0."""
    if price <= 0 or price >= 1:
        return 0.0
    rate = fee_rate if fee_rate is not None else _DEFAULT_PM_RATE
    return rate * contracts * price * (1.0 - price)
```

### New Gemini Fee Function
```python
# Source: gemini.com/fees/predictions (effective March 18, 2026)
# Old: min(P, 1-P) * fee_rate
# New: fee_rate * C * P * (1 - P)

GEMINI_TAKER_RATE = _env_float("GEMINI_TAKER_FEE_RATE", "0.07")
GEMINI_MAKER_RATE = _env_float("GEMINI_MAKER_FEE_RATE", "0.0175")

def gemini_fee(price: float, fee_rate: float = 0.07, contracts: int = 1) -> float:
    """Gemini fee: fee_rate * C * P * (1 - P). Rounded up to next cent."""
    if price <= 0 or price >= 1:
        return 0.0
    import math
    raw = fee_rate * contracts * price * (1.0 - price)
    return math.ceil(raw * 100) / 100  # Round up to next cent
```

### Kalshi Maker Fee Function (New)
```python
# Source: kalshi.com/fee-schedule (2026)
# Maker: ceil(0.0175 * C * P * (1-P)) per contract in cents

def kalshi_maker_fee(price: float, contracts: int = 1) -> float:
    """Kalshi maker fee -- lower than taker. Returns total in dollars."""
    if price <= 0 or price >= 1:
        return 0.0
    fee_cents = max(1, math.ceil(1.75 * price * (1 - price)))
    fee_cents = min(fee_cents, KALSHI_FEE_CAP_CENTS)
    return (fee_cents * contracts) / 100.0
```

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 |
| Config file | none (default) |
| Quick run command | `pytest tests/test_fees.py tests/test_executor.py -v -x` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EXEC-01 | Revalidation pass rate 5-30% with layer floors | unit | `pytest tests/test_executor.py -k "revalidat" -v -x` | Partial -- needs layer-aware tests |
| EXEC-02 | Maker/limit routing on PM and Kalshi | unit | `pytest tests/test_executor.py -k "maker or gtc" -v -x` | Partial -- GTC tests exist, need maker-specific |
| EXEC-03 | Layer-specific revalidation floors | unit | `pytest tests/test_executor.py -k "layer" -v -x` | Wave 0 |
| EXEC-04 | Fee calculations match 2026 schedules | unit+integration | `pytest tests/test_fees.py -v -x && python tests/integration/verify_fees.py` | Exists but uses OLD formulas |
| EXEC-07 | Profitable round-trip trade | manual/integration | Manual -- requires live credentials | Manual-only |

### Wave 0 Gaps
- [ ] `tests/test_fees.py` -- update all Polymarket tests for new `P*(1-P)*rate` formula
- [ ] `tests/test_fees.py` -- update Gemini tests for new formula and rates
- [ ] `tests/test_fees.py` -- add `kalshi_maker_fee` tests
- [ ] `tests/test_executor.py` -- add layer-aware revalidation floor tests
- [ ] `tests/test_executor.py` -- add maker routing tests (timeout, cancel behavior)
- [ ] `tests/integration/verify_fees.py` -- complete rewrite with new Polymarket/Gemini formulas
- [ ] Config validation for new `REVAL_FLOOR_L*` env vars

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | All | Yes | 3.14.3 | -- |
| pytest | Testing | Yes | 9.0.2 | -- |
| Railway CLI | Deployment | N/A (GitHub integration) | -- | Push to master triggers deploy |

Step 2.6: Mostly code/config changes with no new external dependencies. Railway deployment is via existing GitHub integration.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Polymarket 2% on net winnings | Dynamic taker fee `rate * P * (1-P)` | March 2026 | **BREAKING** -- entire fee model wrong |
| Polymarket zero maker fee | Zero maker fee + rebates (25-50%) | March 2026 | Maker routing now saves ALL PM fees |
| Gemini `min(P,1-P) * 0.05` taker | `0.07 * P * (1-P)` taker | March 18, 2026 | Formula and rate both changed |
| Gemini `min(P,1-P) * 0.01` maker | `0.0175 * P * (1-P)` maker | March 18, 2026 | Formula and rate both changed |
| Kalshi taker-only fees | Taker (0.07) + Maker (0.0175) | July 2025 | Maker fee exists -- affects maker routing cost savings |
| IBKR $0.01 exchange fee | $0.01 built into spread (Yes+No=$1.01) | Unchanged | $0.00 commission confirmed; 3.14% APY coupon on positions |

**Deprecated/outdated in codebase:**
- `polymarket_fee()` function: Uses 2% winner fee model, must be replaced
- `GEMINI_FEE_RATE=0.05` default: Must be updated to 0.07
- `gemini_fee()` formula: Uses `min(P,1-P)*rate`, must use `P*(1-P)*rate`
- `PLATFORM_FEE_SCHEDULE` dict in `fees.py`: Polymarket entry shows `"taker": 0.02` (winner fee), should be `0.04` (category-dependent entry fee)

## Open Questions

1. **Polymarket market category in API response**
   - What we know: Fee depends on market category. Category rates are documented.
   - What's unclear: Does the Polymarket CLOB API return category metadata with market data? If so, what field name?
   - Recommendation: For Phase 5, default to "politics" (0.04) with env-var override. Add API-based category detection later.

2. **Matchbook post-promo fee rate for prediction markets**
   - What we know: 0% promo for 110 days from account creation. Standard exchange is 1.5%.
   - What's unclear: Whether prediction markets specifically will have 0% permanently or revert to some rate.
   - Recommendation: Keep 0% default, add `MATCHBOOK_PREDICTION_COMMISSION` env-var override.

3. **Kalshi S&P/Nasdaq halved fee rate**
   - What we know: S&P 500 and Nasdaq-100 markets use 0.035 multiplier instead of 0.07.
   - What's unclear: How to detect which Kalshi markets are S&P/Nasdaq programmatically.
   - Recommendation: Out of scope for Phase 5. Standard 0.07 rate covers all markets conservatively.

## Sources

### Primary (HIGH confidence)
- [Polymarket Fees Documentation](https://docs.polymarket.com/trading/fees) -- Category-specific fee rates, formula: `feeRate * C * P * (1-P)`, zero maker fees
- [Polymarket US DCM Fee Schedule](https://www.polymarketexchange.com/fees-hours.html) -- 30 bps taker / 20 bps maker rebate (US regulated platform, different from Global)
- [Gemini Predictions Fee Schedule](https://www.gemini.com/fees/predictions) -- Effective March 18, 2026: taker 0.07, maker 0.0175, formula `rate * C * P * (1-P)`
- [Kalshi Help Center - Fees](https://help.kalshi.com/trading/fees) -- Taker: `ceil(0.07 * C * P * (1-P))`, maker: `ceil(0.0175 * C * P * (1-P))`
- [Betfair Commission FAQ](https://support.betfair.com/app/answers/detail/413-exchange-what-is-commission-and-how-is-it-calculated/) -- 2-5% on net winnings, discount tiers available
- [Smarkets Commission FAQ](https://help.smarkets.com/hc/en-gb/articles/212654665-Smarkets-commission-FAQ) -- 2% standard, 1% pro, 3% select
- [IBKR ForecastEx Commissions](https://www.interactivebrokers.com/en/pricing/commissions-events.php) -- $0.00 commission, $0.01 exchange fee in spread
- [SX Bet Fees](https://help.sx.bet/en/articles/2798017-sx-bet-fees) -- 0% on single bets

### Secondary (MEDIUM confidence)
- [Polymarket Fee Expansion March 30, 2026](https://coincu.com/news/polymarket-fee-expansion-march-30-2026/) -- Category expansion details, peak rates by category
- [DeFi Rate Prediction Market Fees Comparison](https://defirate.com/prediction-markets/fees/) -- Cross-platform fee comparison, Kalshi maker formula confirmed
- [KuCoin Polymarket Fees Guide 2026](https://www.kucoin.com/blog/polymarket-fees-trading-guide-2026) -- Confirms dynamic taker model, zero settlement fee

### Tertiary (LOW confidence)
- [Matchbook Prediction Market Commission](https://caanberry.com/matchbook-review-commission-calculated/) -- Post-promo rate unclear for prediction markets specifically

## Metadata

**Confidence breakdown:**
- Fee audit: HIGH -- verified against official platform fee pages with multiple cross-references. Critical finding: Polymarket and Gemini models changed.
- Revalidation architecture: HIGH -- existing code patterns are well-documented, layer mapping already exists in two places.
- Maker routing: HIGH -- GTC logic already partially implemented in executor, just needs to be the default path.
- First trade criteria: HIGH -- config values (MAX_TRADE_SIZE, DAILY_LOSS_LIMIT) are straightforward env-var changes.

**Research date:** 2026-04-01
**Valid until:** 2026-04-30 (fee schedules may change -- monitor platform announcements)
