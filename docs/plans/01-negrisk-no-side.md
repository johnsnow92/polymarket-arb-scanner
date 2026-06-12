# Plan 01 — NegRisk NO-side arbitrage

**Strategy class:** Layer 1, pure arbitrage (risk-free, modulo UMA dispute tail → see Plan 05).
**Effort:** Low (extends modules you already own).
**Flag:** `NEGRISK_NO_SIDE_ENABLED` (default `false`).
**Ships first.**

## Mechanism

A Polymarket NegRisk event is a set of **N mutually-exclusive binary outcome markets** where exactly one outcome resolves YES. If you buy **1 NO share on every outcome**, then exactly **(N−1)** of those NO shares win $1.00 (only the single winning outcome's NO loses). The payout is therefore **always exactly $(N−1)**, regardless of which outcome wins.

So the arbitrage condition is:

```
Σ(no_ask_i)  <  (N − 1) − fees − gas      ⟹  risk-free profit
```

The existing `scans/negrisk.py` only checks the **YES side** (`Σ yes_ask < 1.0`). The NO side is uncovered, and on-chain analysis of the documented $29M NegRisk extraction shows the NO-side + rebalance was the **larger ~60%** of the profit pool. This plan adds the held-to-resolution NO-side scan. The capital-efficient *convert/rebalance* version (recover capital immediately via `convertPositions` instead of waiting for settlement) is **Plan 04** and depends on the CTF subsystem.

## Risk caveats (be honest)

- **Risk-free at resolution** — same class as the existing YES-side arb. The only tail is a UMA dispute flipping a "certain" outcome or freezing capital 4–6 days → mitigate with Plan 05's dispute gate before scaling.
- **Capital-heavy, low ROI** — total outlay ≈ $(N−1) for a payout of $(N−1), so `net_roi = net_profit / Σ(no)` is small for large N. This is expected; the convert path (Plan 04) is what makes it capital-efficient. Keep `MAX_TRADE_SIZE` modest and let `capital_efficiency_score()` rank it.
- **Long-tail NO liquidity** — NO books on no-hope outcomes can be thin; the Stage-2 CLOB refine + existing depth gate (`RiskManager` `min_liquidity`) handle this. Require ≥50% CLOB coverage like the YES-side refiner.

## Files to touch

| File | Change |
|------|--------|
| `fees.py` | add `net_profit_negrisk_no_side()` |
| `scans/negrisk.py` | add `scan_negrisk_no_side()` + `_refine_negrisk_no_side_with_clob()` |
| `executor.py` | `_build_legs` NegRiskNO branch (**above** the NegRisk branch); `_revalidate` dispatch (**above** NegRisk); new `_revalidate_negrisk_no()` |
| `cli.py` | import, parallel-scan submit, argparse choice |
| `config.py` | `NEGRISK_NO_SIDE_ENABLED` flag |
| `tests/test_fees.py`, `tests/test_negrisk_no_side.py` | coverage |
| `docs/strategy-framework-v2.md`, `CLAUDE.md` | register strategy + mode |

---

## Task 1 — `fees.py`: NO-side profit function

Insert directly after `net_profit_negrisk_internal` (ends ~line 162):

```python
def net_profit_negrisk_no_side(no_prices: list[float]) -> dict:
    """Net profit for a NegRisk NO-side arbitrage.

    Buy one NO share of every outcome in a mutually-exclusive N-outcome event.
    Exactly one outcome resolves YES, so exactly (N-1) of the NO shares pay $1.00.
    Guaranteed payout = (N - 1). Profit = (N - 1) - sum(no_prices) - fees.

    March 2026 model: every leg pays the Polymarket taker entry fee at trade time.
    """
    n = len(no_prices)
    if n < 2:
        return {"gross_spread": 0.0, "fees": 0, "net_profit": 0.0}

    total_cost = sum(no_prices)
    gross_spread = float(n - 1) - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    fee = sum(polymarket_taker_fee(p) for p in no_prices)
    gas = POLYGON_GAS_ESTIMATE * n

    return {
        "gross_spread": gross_spread,
        "fees": fee + gas,
        "net_profit": gross_spread - fee - gas,
    }
```

`polymarket_taker_fee` and `POLYGON_GAS_ESTIMATE` are already in scope in `fees.py` (used by `net_profit_negrisk_internal`).

## Task 2 — `scans/negrisk.py`: scan + refine

Add the import at the top (line 7 already imports `net_profit_negrisk_internal`):

```python
from fees import net_profit_negrisk_internal, net_profit_negrisk_no_side
```

Append these two functions to the module. They mirror the YES-side pair but use NO mid/ask prices (`parse_outcome_prices(m)[1]`, `clob["no_ask"]`) and the NO token (`_extract_token_ids(m)[1]`). **Store the full NO-price list on `_no_prices`** so the executor reads a real list instead of re-parsing the truncated `prices` summary string (this also avoids the latent N>5 parse bug in the YES-side `_build_legs` branch — see side-finding below).

```python
def scan_negrisk_no_side(events: list[dict], min_profit: float,
                         price_cache: dict | None = None) -> list[dict]:
    """Scan for NegRisk NO-side arbitrage (buy all NO when Σ NO < N-1)."""
    from config import NEGRISK_NO_SIDE_ENABLED
    if not NEGRISK_NO_SIDE_ENABLED:
        return []

    opportunities = []
    events_by_title = {}
    negrisk_events = get_negrisk_events(events)
    logger.info("Scanning %d NegRisk events (NO-side)...", len(negrisk_events))

    for event in negrisk_events:
        markets = event.get("markets", [])
        if len(markets) < 2:
            continue

        no_prices = []
        no_token_ids = []
        valid = True
        for m in markets:
            if not _within_resolution_window(m, platform="polymarket"):
                valid = False
                break
            prices = parse_outcome_prices(m)
            if not prices or len(prices) < 2:
                valid = False
                break
            no_price = prices[1]          # index 0 = YES, index 1 = NO
            if no_price <= 0:
                valid = False
                break
            no_prices.append(no_price)
            tids = _extract_token_ids(m)
            no_token_ids.append(tids[1] if len(tids) > 1 else "")

        if not valid or len(no_prices) < 2:
            continue

        result = net_profit_negrisk_no_side(no_prices)
        if result["net_profit"] >= min_profit:
            n = len(no_prices)
            total = sum(no_prices)
            price_summary = ", ".join(f"{p:.3f}" for p in sorted(no_prices, reverse=True)[:5])
            if n > 5:
                price_summary += f"... ({n} total)"
            event_key = event.get("id", event.get("title", ""))
            events_by_title[event_key] = event
            opportunities.append({
                "type": f"NegRiskNO({n})",
                "_layer": 1,
                "market": event.get("title", "Unknown")[:60],
                "prices": price_summary,
                "total_cost": f"${total:.4f}",
                "gross_spread": f"{result['gross_spread']:.4f}",
                "fees": f"${result['fees']:.4f}",
                "net_profit": result["net_profit"],
                "net_roi": f"{result['net_profit'] / total * 100:.2f}%",
                "volume": f"${sum(float(m.get('volume', 0) or 0) for m in markets):,.0f}",
                "_event_key": event_key,
                "_token_ids": no_token_ids,     # NO tokens
                "_no_prices": list(no_prices),  # full list for execution
                "_days_to_resolution": _days_to_resolution(markets[0], "polymarket"),
            })

    opportunities = _refine_negrisk_no_side_with_clob(
        opportunities, events_by_title, min_profit, price_cache=price_cache)
    opportunities = filter_dust(opportunities)
    return opportunities


def _refine_negrisk_no_side_with_clob(opportunities: list[dict], events_by_title: dict,
                                      min_profit: float, price_cache: dict | None = None) -> list[dict]:
    """Stage 2: re-check NegRisk NO-side candidates using CLOB NO-ask prices."""
    if not opportunities:
        return opportunities

    all_markets = []
    for opp in opportunities:
        event = events_by_title.get(opp.get("_event_key"))
        if event:
            all_markets.extend(event.get("markets", []))

    clob_cache = {}
    if all_markets:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_fetch_clob_for_market, m, price_cache): m for m in all_markets}
            for future in as_completed(futures):
                try:
                    market, clob = future.result()
                    clob_cache[id(market)] = clob
                except Exception as e:
                    logger.debug("CLOB fetch failed for NegRiskNO refinement: %s", e)

    refined = []
    for opp in opportunities:
        event = events_by_title.get(opp.get("_event_key"))
        if not event:
            refined.append(opp)
            continue
        markets = event.get("markets", [])
        no_asks = []
        min_depth = float("inf")
        clob_count = 0
        for m in markets:
            clob = clob_cache.get(id(m))
            if clob and clob.get("no_ask") is not None:
                no_asks.append(clob["no_ask"])
                clob_count += 1
                min_depth = min(min_depth, clob.get("no_ask_size") or 0)
            else:
                prices = parse_outcome_prices(m)
                no_asks.append((prices[1] + 0.01) if prices and len(prices) > 1 else None)

        if None in no_asks:
            opp["_clob_refined"] = False
            refined.append(opp)
            continue
        if (clob_count / len(markets) if markets else 0) < 0.5:
            opp["_clob_refined"] = False
            refined.append(opp)
            continue

        result = net_profit_negrisk_no_side(no_asks)
        if result["net_profit"] >= min_profit:
            total = sum(no_asks)
            opp["total_cost"] = f"${total:.4f}"
            opp["net_profit"] = result["net_profit"]
            opp["net_roi"] = f"{result['net_profit'] / total * 100:.2f}%"
            opp["_no_prices"] = list(no_asks)
            opp["_clob_depth"] = min_depth if min_depth != float("inf") else 0
            opp["_clob_coverage"] = f"{clob_count}/{len(markets)}"
            refined.append(opp)
        else:
            logger.info("NegRiskNO dropped at ask: %s net=$%.4f",
                        opp.get("market", "?")[:40], result["net_profit"])
    return refined
```

## Task 3 — `executor.py`: build legs + revalidate

**3a. `_build_legs` (line 1360).** Insert the NegRiskNO branch **immediately before** `elif opp_type.startswith("NegRisk"):` (line 1400) — order matters because `"NegRiskNO".startswith("NegRisk")` is `True`:

```python
        elif opp_type.startswith("NegRiskNO"):
            # Buy NO on each outcome (Σ NO < N-1 arbitrage)
            no_prices = opportunity.get("_no_prices", [])
            if token_ids and len(no_prices) != len(token_ids):
                logger.warning(f"NegRiskNO price/token mismatch ({len(no_prices)} vs {len(token_ids)}). Skipping.")
                return []
            for i, price in enumerate(no_prices):
                legs.append({
                    "platform": "polymarket",
                    "side": "BUY",
                    "token": f"no_{i}",
                    "price": price,
                    "_token_id": token_ids[i] if i < len(token_ids) else "",
                })
```

The Polymarket execution path (`_execute_single_leg`) keys off `_token_id` + `side="BUY"`, so buying the NO token by its token ID is correct.

**3b. `_revalidate` dispatch (line 396).** Insert **before** `elif opp_type.startswith("NegRisk"):`:

```python
            elif opp_type.startswith("NegRiskNO"):
                passed, reval_profit, reason = self._revalidate_negrisk_no(
                    opportunity, original_profit, price_cache)
```

**3c. New `_revalidate_negrisk_no`** — copy `_revalidate_negrisk` (line 703) verbatim, rename, and change the single profit call from `net_profit_negrisk_internal(yes_asks)` to `net_profit_negrisk_no_side(asks)` (the `_token_ids` on this opp are the NO tokens, so the fetched asks are already NO asks). Add `net_profit_negrisk_no_side` to the `fees` import at the top of `executor.py`.

**3d. No change needed** at executor.py:1275 (`startswith("NegRisk")` count-parse — the `\((\d+)\)` regex correctly extracts N from `"NegRiskNO(5)"`) or executor.py:2663 (`"NegRisk" in type` neg-risk fee flag — correctly `True` for the NO side). Verify both with the test in Task 6.

## Task 4 — `cli.py`: wire the scan

- Add to the import block (~line 46): `scan_negrisk_no_side,`
- Add the flag import where config flags are imported.
- In `_run_oneshot`, directly after the `negrisk` submit (line 155), add:

```python
            if NEGRISK_NO_SIDE_ENABLED:
                scan_futures["negrisk_no"] = pool.submit(scan_negrisk_no_side, poly_events, min_profit)
```

  (mirror exactly how `scan_futures["negrisk"]` results are later collected into `all_opportunities`).
- Add `"negrisk-no"` to the argparse `choices` list (cli.py:1095-1103) and to the `--mode` help string.
- **Continuous mode:** add the same gated submit to `continuous.py`'s scan set, mirroring how `negrisk` runs there.

## Task 5 — `config.py`: flag

Add next to the other Layer-1 / NegRisk constants:

```python
# S1: NegRisk NO-side arbitrage (buy all NO when Σ NO < N-1)
NEGRISK_NO_SIDE_ENABLED = _env_bool("NEGRISK_NO_SIDE_ENABLED", "false")
```

(No `validate_config()` change needed — it's a plain bool.)

## Task 6 — Tests

**`tests/test_fees.py`** — add a class (methods-in-class convention):

```python
class TestNegRiskNoSide:
    def test_profitable_three_outcome(self):
        # 3 outcomes, pay 1.85 for guaranteed $2.00 payout
        r = net_profit_negrisk_no_side([0.90, 0.55, 0.40])
        assert r["gross_spread"] == pytest.approx(0.15, abs=1e-9)
        assert r["net_profit"] > 0
        assert r["net_profit"] < r["gross_spread"]   # fees + gas subtracted

    def test_not_profitable_when_sum_at_floor(self):
        # Σ NO == N-1 → no edge
        r = net_profit_negrisk_no_side([1.0, 1.0])     # N=2, payout=1, cost=2
        assert r["net_profit"] <= 0

    def test_degenerate_single_outcome(self):
        assert net_profit_negrisk_no_side([0.5]) == {"gross_spread": 0.0, "fees": 0, "net_profit": 0.0}
```

**`tests/test_negrisk_no_side.py`** — mirror the existing NegRisk scan test: build a fake `events` list of NegRisk events with `outcomePrices` (NO at index 1) summing below N−1, mock `_fetch_clob_for_market` to return `no_ask`/`no_ask_size`, assert one `NegRiskNO(n)` opp with `_no_prices` length == n and `_token_ids` length == n. Set `NEGRISK_NO_SIDE_ENABLED=true` via monkeypatch on the config import. Follow the `sys.modules` SDK-stub pattern from `test_executor.py`.

## Verification

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/test_fees.py -k NegRiskNoSide -v          # expect 3 passed
pytest tests/test_negrisk_no_side.py -v                # expect green
NEGRISK_NO_SIDE_ENABLED=true python scanner.py --mode negrisk-no --dry-run   # detect-only smoke
pytest tests/ -q                                        # full suite still green
```

## Done criteria

- `net_profit_negrisk_no_side` covered by tests (profitable / floor / degenerate).
- `--mode negrisk-no` runs in one-shot and continuous, gated by `NEGRISK_NO_SIDE_ENABLED`, default off.
- A synthetic Σ-NO-below-(N−1) event produces a `NegRiskNO(n)` opp that survives `_build_legs` (legs length == n, correct NO token IDs) and `_revalidate_negrisk_no`.
- Full suite green; strategy registered in `docs/strategy-framework-v2.md` and the CLAUDE.md mode list.

## Side-finding (log separately, do not fix in this PR)

The existing YES-side `_build_legs` NegRisk branch (executor.py:1400) re-parses `opportunity["prices"]`, which for **N>5** outcomes is a truncated summary string (`"... (N total)"`). The parse yields fewer prices than `_token_ids`, tripping the `len mismatch → return []` guard — so **YES-side NegRisk execution silently no-ops for events with >5 outcomes**. This NO-side plan sidesteps it via the dedicated `_no_prices` list. Recommend a follow-up that migrates the YES-side branch to a `_yes_prices` list the same way.
