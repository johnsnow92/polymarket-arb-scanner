# Plan 03 — Cross-date / nested temporal arbitrage

**Strategy class:** Layer 1/2, near-risk-free.
**Effort:** Medium.
**Flag:** `TEMPORAL_ARB_ENABLED` (default `false`).
**Depends on:** Plan 02 (reuses the implication-lock fee fn + executor leg shape).

## Mechanism

For a **cumulative "by-deadline" market** ("X ≥ K by date D"), probability is **monotonically non-decreasing in the deadline**: a later deadline can only make the event more likely. So for the same `(underlying, strike)`:

```
P("X ≥ K by D_early")  ≤  P("X ≥ K by D_late")        when D_early < D_late
```

This is exactly the **implication lock of Plan 02** (`"by D_early" ⊆ "by D_late"`). When the earlier-deadline contract trades *above* the later one, **buy YES(later) + NO(earlier)** for a locked spread of `P(early) − P(late) − fees`.

So temporal arb = Plan 02's lock, with **date-based discovery** instead of strike-based. This plan is mostly a discovery module; it reuses `net_profit_frechet_implication` and a near-identical executor leg.

## Critical scoping (the failure mode)

**Only `by/before/touch` (cumulative) markets are monotonic across dates.** "Price *at expiry* D" range markets are **not** nested across dates and must be excluded — pairing two "at-expiry" markets is a naked position, not a lock. The discovery classifier must positively identify the cumulative structure and reject at-expiry/range markets. When in doubt, drop the pair.

Other caveats:
- **Capital lockup to the later date** — `_days_to_resolution = max(both)`.
- **Same platform + same settlement source only** in v1 (Kalshi's structured series are ideal: identical index, identical settlement). Cross-platform/cross-source temporal pairs add basis risk — defer.

## Why it's new

`scans/time_decay.py` handles single-market near-expiry consensus, and `scans/bracket.py` handles same-date range partitions — neither enforces **cross-date** monotonicity. No module groups a dated series by `(asset, strike)` across resolution dates.

## Files to touch

| File | Change |
|------|--------|
| `kalshi_ticker.py` (new, small) | parse `KX<ASSET>-<YYMMMDD[HH]>-T<strike>` → `(asset, datetime, strike, is_cumulative)` |
| `scans/temporal.py` (new) | group by `(asset, strike)`, sort by date, flag violations, two-stage refine |
| `fees.py` | reuse `net_profit_frechet_implication` (Plan 02) |
| `executor.py` | `TemporalArb` branch (buy YES_late + NO_early) + `_revalidate_temporal` |
| `cli.py`, `config.py`, tests, docs | wire + flag + cover |

---

## Task 1 — `kalshi_ticker.py` (new)

Kalshi tickers are structured (confirmed in `kalshi_api.py` docstrings: `KXBTC-26FEB07-T101999.99`, `KXBTC-26APR2717-T87749.99`). Parse them:

```python
import re
from datetime import datetime, timezone

_MONTHS = {m: i for i, m in enumerate(
    ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"], start=1)}
_TICKER_RE = re.compile(r"^KX([A-Z]+?)-(\d{2})([A-Z]{3})(\d{2})(\d{2})?-T([\d.]+)$")

def parse_kalshi_ticker(ticker: str) -> dict | None:
    """Parse 'KX<ASSET>-<YYMMMDD[HH]>-T<strike>' → asset/datetime/strike, or None."""
    m = _TICKER_RE.match(ticker or "")
    if not m:
        return None
    asset, yy, mon, dd, hh, strike = m.groups()
    if mon not in _MONTHS:
        return None
    try:
        dt = datetime(2000 + int(yy), _MONTHS[mon], int(dd),
                      int(hh) if hh else 0, tzinfo=timezone.utc)
        return {"asset": asset, "datetime": dt, "strike": float(strike), "ticker": ticker}
    except ValueError:
        return None
```

**Cumulative-vs-at-expiry classification:** Kalshi crypto "above/touch" series vs "range/close" series differ by series prefix/subtitle. v1 rule: treat a market as cumulative only if its `title`/`subtitle` contains `by`, `before`, `reach`, `touch`, or `hit` AND it is a single-sided `≥ strike` (`yes_sub_title` like `"Yes"` for "≥K"). Anything with an explicit range (`between`, `X-Y`) or "at <time>" is excluded. Encode as `is_cumulative(market) -> bool`; **default False** (conservative).

## Task 2 — `scans/temporal.py` (new)

```python
def scan_temporal_arb(markets: list[dict], min_profit: float = 0.005,
                      price_cache: dict | None = None) -> list[dict]:
    """Detect cross-date monotonicity violations on cumulative 'by-deadline' series."""
    from config import TEMPORAL_ARB_ENABLED, TEMPORAL_MIN_VIOLATION
    if not TEMPORAL_ARB_ENABLED:
        return []
    # 1. parse + filter to cumulative markets
    # 2. group by (asset, strike); within a group sort by datetime
    # 3. for each adjacent (early, late) where date_early < date_late:
    #       p_early, p_late = mid YES of each
    #       if p_early - p_late >= TEMPORAL_MIN_VIOLATION:
    #           result = net_profit_frechet_implication(p_a=p_early, p_b=p_late)
    #           emit TemporalArb opp if result['net_profit'] >= min_profit
```

Opp dict mirrors `FrechetArb` but `type="TemporalArb"`, `_layer=1`, and adds `_early_ticker`, `_late_ticker`, `_strike`, `_asset`. Legs: buy **YES on the later-deadline** market (`_buy_yes_token`) + **NO on the earlier-deadline** market (`_buy_no_token`). For Kalshi, legs carry `_ticker` + `side` instead of `_token_id` (mirror the `KalshiMulti` leg shape in `_build_legs`).

Stage-2 refine `_refine_temporal_with_clob`: re-fetch live asks (Kalshi `get_market_price` / orderbook), recompute, drop if it no longer clears `min_profit`.

## Task 3 — `executor.py`

`_build_legs` new exact-match branch (Kalshi two-leg version):

```python
        elif opp_type == "TemporalArb":
            legs = [
                {"platform": "kalshi", "side": "yes", "action": "buy",
                 "price": opportunity["_p_late"], "_ticker": opportunity["_late_ticker"]},
                {"platform": "kalshi", "side": "no", "action": "buy",
                 "price": 1.0 - opportunity["_p_early"], "_ticker": opportunity["_early_ticker"]},
            ]
```

`_revalidate` dispatch: `elif opp_type == "TemporalArb":` → `_revalidate_temporal` (re-fetch both tickers' prices, rerun `net_profit_frechet_implication`). Kalshi prices are live order book, so this can also be a `reason = "live_orderbook"` pass-through if you prefer to match the existing Kalshi handling.

## Task 4 — `config.py` / `cli.py`

```python
TEMPORAL_ARB_ENABLED = _env_bool("TEMPORAL_ARB_ENABLED", "false")
TEMPORAL_MIN_VIOLATION = _env_float("TEMPORAL_MIN_VIOLATION", "0.02")
```

`cli.py`: add `"temporal"` to argparse `choices`; dispatch in `_run_oneshot` (needs Kalshi markets — fetch via the existing Kalshi client path, then `scan_temporal_arb(kalshi_markets, min_profit)`). Wire `continuous.py` with **current** signatures.

## Task 5 — Tests

- `tests/test_kalshi_ticker.py` — parse `KXBTC-26FEB07-T101999.99` → `asset="BTC"`, `datetime(2026,2,7)`, `strike=101999.99`; parse the `…2717…` hour form; reject malformed.
- `tests/test_temporal.py` — two cumulative markets same `(asset, strike)`, earlier @0.60 > later @0.50 → one `TemporalArb`; assert legs (YES late + NO early). Negative: an "at-expiry/range" market pair → no opp (classifier rejects).

## Verification

```bash
pytest tests/test_kalshi_ticker.py tests/test_temporal.py -v
TEMPORAL_ARB_ENABLED=true python scanner.py --mode temporal --dry-run
pytest tests/ -q
```

## Done criteria

- Dated Kalshi series grouped by `(asset, strike)`; only **cumulative** markets considered; monotonicity violations emitted as locked `TemporalArb` (YES_late + NO_early).
- At-expiry/range markets provably excluded (negative test passes).
- Capital-lockup-to-later-date surfaced via `_days_to_resolution`.
- Registered; suite green. Phase-2 backlog: Polymarket dated "by date" markets; cross-platform temporal (with basis-risk handling).
