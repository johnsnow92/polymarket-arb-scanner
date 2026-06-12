# Plan 02 — Fréchet-bound logical / conditional arbitrage

**Strategy class:** Layer 1, pure arbitrage (risk-free).
**Effort:** Low–Medium (generalizes existing `logical_arb.py` / `conditional.py`).
**Flag:** `FRECHET_ARB_ENABLED` (default `false`).

## Mechanism

Two related markets must obey **coherence bounds** regardless of correlation. For events A and B with market YES-prices `P(A)`, `P(B)`:

- **Implication / subset (A ⊆ B):** if A occurring forces B (e.g. "BTC > $100k" ⊆ "BTC > $90k", or "wins nomination" ⊇ "wins presidency"), then `P(A) ≤ P(B)`. A violation `P(A) > P(B)` is a **locked dutch book**.
- **Fréchet inequalities (general pair):** for any two events, `max(0, P(A)+P(B)−1) ≤ P(A∧B) ≤ min(P(A), P(B))`. When a joint market `P(A∧B)` is tradeable and sits outside these bounds, the violation is arbitrageable.

### The implication lock (the concrete, risk-free core)

When `P(A) > P(B)` for `A ⊆ B`, buy **YES on B** and **NO on A**. Payoffs across the only three reachable states (A⊆B forbids A=1,B=0):

| state | YES_B | NO_A | total |
|---|---|---|---|
| A=1, B=1 | 1 | 0 | **1** |
| A=0, B=1 | 1 | 1 | **2** |
| A=0, B=0 | 0 | 1 | **1** |

Minimum payoff = **$1.00**. Cost = `P(B) + (1 − P(A)) = 1 + P(B) − P(A)`. When `P(A) > P(B)`, cost < $1 ≤ min payoff ⟹ **risk-free profit ≥ P(A) − P(B) − fees**.

### Why this is new (verified)

`scans/logical_arb.py` already detects the *condition* `P(then) < P(if)·(1−threshold)` but: (a) it is **rule-driven only** — pairs come from a hand-authored `logical_arb_rules.json`, none auto-discovered; (b) its executor wiring is **incomplete** — `_build_legs` (executor.py:1877) expects `_token_ids[1]` to be the `if`-market hedge token, but the scan only populates `_token_ids` from the `then` market (so `[1]` is the *then*-market NO, not the `if`-market token); (c) it is **not dispatched in one-shot** (`cli.py`) at all, only in `continuous.py`. `scans/conditional.py` (the `P(X|Y)·P(Y)` triplet) is fully **orphaned** (not in argparse `choices`, dispatched nowhere). This plan delivers a correct, auto-discovering, properly-wired version.

## Risk caveats

- **Risk-free** when both legs are on the **same platform with the same settlement source** (no basis risk). Cross-platform/cross-source pairs add resolution-mismatch risk — restrict the first version to same-platform pairs.
- **Capital lockup until both resolve** — the two legs may resolve on different dates; carry the locked spread to the later one. Surface via `_days_to_resolution = max(legs)`.
- **Mis-paired markets are the failure mode** — a wrong subset/implication classification turns a "lock" into a naked position. Gate auto-discovered pairs behind a high-confidence classifier (structured-title parse, not fuzzy guesswork) and keep a manual allowlist for the rest.

## Files to touch

| File | Change |
|------|--------|
| `fees.py` | add `net_profit_frechet_implication(p_a, p_b)` |
| `scans/frechet.py` (new) | pair discovery + bound check + two-stage refine |
| `pair_relations.py` (new, small) | subset/implication classifier from structured titles + manual rules |
| `executor.py` | `_build_legs` `FrechetArb` branch (2 legs: YES_B + NO_A); `_revalidate_frechet` |
| `cli.py`, `config.py` | wire + flag |
| tests, docs | coverage + registration |

---

## Task 1 — `fees.py`

```python
def net_profit_frechet_implication(p_a: float, p_b: float) -> dict:
    """Net profit for an implication-violation lock on A ⊆ B (so P(A) ≤ P(B) must hold).

    When P(A) > P(B): buy YES_B + NO_A. Min payoff $1.00, cost = 1 + P(B) - P(A).
    Profit = (P(A) - P(B)) - fees. Both legs pay the Polymarket taker entry fee.
    """
    gross_spread = p_a - p_b
    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}
    fee = polymarket_taker_fee(p_b) + polymarket_taker_fee(1.0 - p_a)
    gas = POLYGON_GAS_ESTIMATE * 2
    return {
        "gross_spread": gross_spread,
        "fees": fee + gas,
        "net_profit": gross_spread - fee - gas,
    }
```

(For the general Fréchet-with-joint-market case add `net_profit_frechet_joint(p_a, p_b, p_ab)` in a phase 2 — the implication lock above covers the highest-frequency, cleanest case and is the recommended first ship.)

## Task 2 — `pair_relations.py` (new): discover A ⊆ B pairs

The engineering core is **finding genuine subset relationships safely**. Three sources, highest-confidence first:

1. **Structured numeric thresholds (same underlying, same date).** Parse `"<underlying> > $<K>"` / `"above/over <K>"` titles (reuse `scans/bracket.py:_parse_bracket` + `RANGE_PATTERNS`) and Kalshi structured tickers `KX<ASSET>-<YYMMMDD[HH]>-T<strike>` (see Plan 03 for the parser). For the same `(underlying, date)`, **higher strike ⊆ lower strike** (BTC>$100k ⊆ BTC>$90k). Emit those pairs with HIGH confidence.
2. **Manual allowlist** — reuse the `logical_arb_rules.json` format (`{"subset": id_A, "superset": id_B}`) for hand-curated semantic pairs ("wins nomination" ⊆ "wins presidency"). MEDIUM-HIGH.
3. **Fuzzy candidate suggestion (advisory only, never auto-traded)** — use `matcher.match_cross_platform` to *surface* possible pairs into a review log; do not execute on these without promotion to the allowlist.

Signature:

```python
def discover_subset_pairs(markets_by_key: dict,
                          manual_rules: list[dict],
                          same_platform_only: bool = True) -> list[dict]:
    """Return [{'sub': market, 'sup': market, 'source': 'strike'|'manual', 'confidence': float}]."""
```

## Task 3 — `scans/frechet.py` (new)

Two-stage, mirroring `scans/bracket.py`:

- **Stage 1** — for each discovered `(sub=A, sup=B)` pair, read mid `P(A)`, `P(B)`; if `P(A) − P(B) ≥ FRECHET_MIN_VIOLATION`, run `net_profit_frechet_implication`; if `net_profit ≥ min_profit`, emit:

```python
{
    "type": "FrechetArb",
    "_layer": 1,
    "market": f"{sub_title[:28]} ⊆ {sup_title[:28]}",
    "prices": f"P(A)={p_a:.3f} > P(B)={p_b:.3f}",
    "total_cost": f"${(1 + p_b - p_a):.4f}",
    "net_profit": result["net_profit"],
    "net_roi": result.get("net_roi", 0),
    "confidence": pair["confidence"],
    "_platform": platform,
    "_buy_yes_market": sup, "_buy_yes_token": _extract_token_ids(sup)[0],
    "_buy_no_market":  sub, "_buy_no_token":  _extract_token_ids(sub)[1],
    "_p_a": p_a, "_p_b": p_b,
    "_token_ids": [_extract_token_ids(sup)[0], _extract_token_ids(sub)[1]],
    "_days_to_resolution": max(_days_to_resolution(sub), _days_to_resolution(sup)),
}
```

- **Stage 2** `_refine_frechet_with_clob` — re-fetch `yes_ask` of B and `no_ask` of A via `_fetch_clob_for_market`, recompute with `net_profit_frechet_implication(p_a_from_no_ask, p_b_from_yes_ask)` where `p_a` used for the NO leg is `1 − no_ask_A`; drop if it no longer clears `min_profit`; set `_clob_depth = min(depth_B_yes, depth_A_no)`.

## Task 4 — `executor.py`

`_build_legs` new branch (anywhere among the `elif`s; the type is exact `"FrechetArb"`):

```python
        elif opp_type == "FrechetArb":
            legs = [
                {"platform": "polymarket", "side": "BUY", "token": "yes",
                 "price": opportunity["_p_b"], "_token_id": opportunity["_buy_yes_token"]},
                {"platform": "polymarket", "side": "BUY", "token": "no",
                 "price": 1.0 - opportunity["_p_a"], "_token_id": opportunity["_buy_no_token"]},
            ]
```

`_revalidate` dispatch: add `elif opp_type == "FrechetArb":` → `_revalidate_frechet`, which re-fetches both legs' asks and reruns the fee fn (mirror `_revalidate_negrisk`, two tokens).

## Task 5 — `cli.py` / `config.py`

```python
# config.py
FRECHET_ARB_ENABLED = _env_bool("FRECHET_ARB_ENABLED", "false")
FRECHET_MIN_VIOLATION = _env_float("FRECHET_MIN_VIOLATION", "0.02")
```

`cli.py`: add `"frechet"` to argparse `choices`; dispatch in `_run_oneshot` following the `correlated` block pattern (build `markets_by_key` keyed by `condition_id`, call `discover_subset_pairs` then `scan_frechet`). Also add to `continuous.py` **using current signatures** (see README gotcha: the existing `correlated`/`time_decay` continuous calls pass stale kwargs and silently die — do not copy those call sites).

## Task 6 — Tests

- `tests/test_fees.py::TestFrechet` — `net_profit_frechet_implication(0.80, 0.60)` → `gross_spread == 0.20`, `net_profit > 0`; `(0.50, 0.60)` → `net_profit <= 0`.
- `tests/test_frechet.py` — strike-parse discovery: two markets "BTC > $100k (Jun 30)" @0.55 and "BTC > $90k (Jun 30)" @0.45 → one `FrechetArb` with `_buy_yes_token` = $90k YES, `_buy_no_token` = $100k NO; assert legs from `_build_legs` are length 2 with those tokens.
- Negative test: same titles but `P(sub) < P(sup)` → no opp.

## Verification

```bash
pytest tests/test_fees.py -k Frechet -v
pytest tests/test_frechet.py -v
FRECHET_ARB_ENABLED=true python scanner.py --mode frechet --dry-run
pytest tests/ -q
```

## Done criteria

- Implication lock detected from **auto-discovered** structured-threshold pairs (not just manual rules), correctly executed as YES_B + NO_A with the right token IDs.
- Same-platform-only by default; cross-platform pairs require explicit opt-in.
- Strategy registered; full suite green.
- Phase-2 backlog noted: general `net_profit_frechet_joint` for pairs with a tradeable A∧B market; promotion workflow for fuzzy-suggested pairs.
