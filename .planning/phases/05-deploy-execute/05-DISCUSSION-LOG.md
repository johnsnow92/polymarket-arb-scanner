# Phase 5: Deploy & Execute - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-01
**Phase:** 05-deploy-execute
**Areas discussed:** Revalidation calibration, Maker routing strategy, Fee verification approach, First trade criteria

---

## Revalidation Calibration

### Q1: How should layer-specific revalidation floors be configured?

| Option | Description | Selected |
|--------|-------------|----------|
| Hardcoded per-layer in config.py | Add REVAL_FLOOR_L1=0.02, etc. as env vars with defaults | |
| Auto-tune from dry-run logs | Run 48-72h dry-run, collect drift distribution, compute 80th-percentile floor | ✓ |
| Hybrid — hardcode then auto-tune | Start with hardcoded, replace with auto-tuned after data accumulates | |

**User's choice:** Auto-tune from dry-run logs
**Notes:** Scientific approach preferred over intuition-based hardcoding.

### Q2: How should the executor know which layer an opportunity belongs to?

| Option | Description | Selected |
|--------|-------------|----------|
| Tag in opportunity dict | Each scan module sets opp['_layer'] = 1-4. Executor reads it. | ✓ |
| Derive from opp type string | Map type strings to layers in executor.py | |

**User's choice:** Tag in opportunity dict
**Notes:** Clean, explicit approach — follows existing `_`-prefixed internal key convention.

### Q3: Initial floors while collecting dry-run data?

| Option | Description | Selected |
|--------|-------------|----------|
| Use roadmap values as starting point | 2%/5%/3%/10% from roadmap, replace after N samples | ✓ |
| Start wide-open (0.3% global) | Maximize data collection, risk if DRY_RUN flipped early | |
| Start conservative (double roadmap) | 4%/10%/6%/20% — fewer passes, zero risk | |

**User's choice:** Use roadmap values as starting point
**Notes:** Roadmap values serve as reasonable initial calibration before data replaces them.

### Q4: How long should the dry-run calibration period be?

| Option | Description | Selected |
|--------|-------------|----------|
| 48 hours minimum | PITFALLS.md recommended range. Weekday + weekend coverage. | |
| 24 hours | Faster to first trade. May miss weekend patterns. | |
| 72 hours | Full 3-day observation. Most conservative. | ✓ |

**User's choice:** 72 hours
**Notes:** Maximum data collection and confidence before risking capital.

---

## Maker Routing Strategy

### Q1: How aggressive should maker (limit) order placement be?

| Option | Description | Selected |
|--------|-------------|----------|
| Best ask minus 1 tick | High fill probability while qualifying as maker | |
| Match best bid (passive) | Lower fill rate, best price. Better for MM (Phase 7). | |
| Mid-price | Split the spread. Moderate fill/price balance. | |
| You decide | Claude picks per layer/urgency | ✓ |

**User's choice:** You decide (Claude's discretion)
**Notes:** Layer-sensitive aggressiveness — time-sensitive L1 arbs more aggressive, L3-4 more passive.

### Q2: What should happen when a maker order doesn't fill?

| Option | Description | Selected |
|--------|-------------|----------|
| Cancel and skip | Cancel after timeout, move on. No taker fallback risk. | ✓ |
| Convert to taker | After timeout, market order remaining quantity. | |
| Re-price and retry once | Cancel, re-price 1 tick more aggressive, try once more. | |

**User's choice:** Cancel and skip
**Notes:** Simplest approach — opportunity is gone, move on.

---

## Fee Verification Approach

### Q1: How should fee rates be verified?

| Option | Description | Selected |
|--------|-------------|----------|
| Manual audit + automated test | Research platform fee pages, update fees.py, add pytest assertions | ✓ |
| Runtime API check | Query fee endpoints at startup, compare to hardcoded. Not all platforms expose. | |
| You decide | Claude handles — most platforms lack fee APIs | |

**User's choice:** Manual audit + automated test
**Notes:** Most realistic approach given limited platform fee APIs.

### Q2: Should fee rates be env-var configurable?

| Option | Description | Selected |
|--------|-------------|----------|
| Hardcoded with env-var override | Defaults in fees.py, env var overrides for hotfixes | ✓ |
| Hardcoded only | Simpler. Fee changes need code deploy. | |
| Env vars only | Max flexibility, harder to audit. | |

**User's choice:** Hardcoded with env-var override
**Notes:** Allows Railway env var hotfix if a platform changes fees mid-day.

---

## First Trade Criteria

### Q1: Which strategy layers eligible for first live trades?

| Option | Description | Selected |
|--------|-------------|----------|
| Layer 1 only (pure arb) | Safest. Validates pipeline before expanding. | |
| Layer 1 + Layer 2 | More opportunity volume, slightly more risk. | |
| All layers simultaneously | Maximum capture, faster data collection. | ✓ |

**User's choice:** All layers simultaneously
**Notes:** Aggressive approach — maximize data collection across all strategies from day 1.

### Q2: Initial maximum trade size?

| Option | Description | Selected |
|--------|-------------|----------|
| $5 per trade | Minimum viable. Covers fees, confirms round-trip P&L. | ✓ |
| $10 per trade | Better fee amortization. | |
| $25 per trade | More meaningful P&L signal. Higher risk. | |

**User's choice:** $5 per trade
**Notes:** Conservative per-trade, combined with all-layers for breadth over depth.

### Q3: Daily loss limit during initial live trading?

| Option | Description | Selected |
|--------|-------------|----------|
| $25/day | 5 losing trades before circuit breaker | ✓ |
| $50/day | 10 losing trades at $5 | |
| $10/day | Very tight, may trigger too early | |

**User's choice:** $25/day
**Notes:** Reasonable headroom for all-layers approach at $5/trade.

---

## Claude's Discretion

- Maker order aggressiveness per strategy layer
- Specific timeout duration for unfilled maker orders (5-10s range)

## Deferred Ideas

None — discussion stayed within phase scope.
