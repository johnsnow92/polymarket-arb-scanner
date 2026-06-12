# Strategy Expansion — Implementation Plans

> Created 2026-06-08. Author: planning pass off `docs/audit/` expansion research.
> **Status: PLANS ONLY — nothing here is implemented.** Each plan is gated on operator go-ahead.
> Every new strategy ships behind a `*_ENABLED=false` feature flag per the CLAUDE.md "first-class strategy" convention.

These six plans implement the strategies recommended in the 2026-06-08 expansion research. They are ordered by **(risk-free certainty × feasibility on this stack)** — ship top-down.

| # | Plan | Risk class | Effort | File |
|---|------|-----------|--------|------|
| 1 | NegRisk NO-side arbitrage (Σ NO < N−1) | Risk-free | Low | [`01-negrisk-no-side.md`](01-negrisk-no-side.md) |
| 2 | Fréchet-bound logical/conditional arbitrage | Risk-free | Low | [`02-frechet-logical-arb.md`](02-frechet-logical-arb.md) |
| 3 | Cross-date / nested temporal arbitrage | Near-risk-free | Medium | [`03-temporal-arb.md`](03-temporal-arb.md) |
| 4 | CTF mint/split & merge/redeem primitives | Risk-free (merge) / near (mint-sell) | High | [`04-ctf-primitives.md`](04-ctf-primitives.md) |
| 5 | UMA oracle dispute-risk gate (defensive) | Defensive control | Medium | [`05-uma-dispute-gate.md`](05-uma-dispute-gate.md) |
| 6 | Limitless/Opinion delta-neutral reward farming | Near-risk-free | Med-High | [`06-reward-farming.md`](06-reward-farming.md) |

## Recommended sequence

1. **S1 + S2** first — both are small edits to modules you already own, both genuinely risk-free, no new infra.
2. **S5 (dispute gate)** before scaling any resolution-held arb — it gates the "risk-free" claim on S1, S3, and the existing resolution/settlement strategies. Build the data-fetch half early even if the gate stays advisory.
3. **S3 (temporal)** — medium build, reuses `bracket.py` parsing + `matcher.py`.
4. **S4 (CTF primitives)** — the big one. New web3 signing/contract-call path. Unlocks the capital-efficient NegRisk *convert* and the mint-and-sell direction. Do after the cheap wins; pairs with S5.
5. **S6 (reward farming)** — depends on a new on-chain venue client (Limitless/Opinion) and shares the EIP-712 work that also un-quarantines SX Bet (#6).

## The "add a new opportunity type" recipe (shared by S1, S2, S3)

Confirmed against the code, this is the canonical pattern every detection plan follows:

1. **Scan** — `scans/<name>.py` with a Stage-1 mid-price `scan_*()` + Stage-2 `_refine_*_with_clob()` (mirror `scans/negrisk.py`). Emit plain opportunity dicts: `type`, `_layer`, `market`, `prices`, `total_cost`, `net_profit`, `net_roi`, `_token_ids`, `_clob_depth`, `_market_key`. Internal keys are `_`-prefixed.
2. **Fees** — `fees.py:net_profit_<name>()` returning `{"gross_spread", "fees", "net_profit"}` (mirror `net_profit_negrisk_no_side`).
3. **Execute** — add a branch in `executor.py:_build_legs()` (switches on `opp["type"]`) and a matching `_revalidate_<name>` case in `executor.py:_revalidate()`.
4. **Wire** — import + dispatch in `cli.py:_run_oneshot()`, add the mode string to the argparse `choices` list (cli.py:1095-1103), and add to `continuous.py` if it should run live.
5. **Flag** — `config.py:<NAME>_ENABLED = _env_bool("<NAME>_ENABLED", "false")` (string default), validate in `validate_config()`.
6. **Test** — `tests/test_*.py`, methods inside classes, SDKs mocked via `sys.modules` (see `test_executor.py`).
7. **Docs** — update `docs/strategy-framework-v2.md` + the CLAUDE.md mode list.

## Cross-cutting gotchas discovered during planning (read before building)

These are real, verified findings that affect more than one plan:

- **`opp_type.startswith("NegRisk")` collision** — both `_build_legs` (executor.py:1400) and `_revalidate` (executor.py:396) match prefix `"NegRisk"`, which also matches `"NegRiskNO"`. Any NegRiskNO branch **must be inserted *above* the existing NegRisk branch** in both places. (S1)
- **`continuous.py` has a silent dead-scan bug** — `continuous.py:~1482-1517` calls `scan_correlated(...)` and `scan_time_decay(...)` with **stale kwargs** (`poly_markets=`, `kalshi_data=`, `correlated_pairs=`, `hours_threshold=`) that no longer match the current signatures (`markets_by_key=`, `signal_aggregator=`, …). A `try/except Exception` swallows the `TypeError`, so these scans **silently no-op in continuous mode today.** Fix this when wiring S2/S3 into continuous mode, or your new scans will die the same way. (S2, S3)
- **`logical-arb` is declared but not dispatched in one-shot** — it's in the argparse `choices` but has zero dispatch in `cli.py:_run_oneshot` (only `continuous.py`). `bracket` and `conditional` modes are fully **orphaned** (not in `choices`, dispatched nowhere). S2/S3 build on these orphans and must wire them in. (S2, S3)
- **`QuoteManager.place_quote` live path is a STUB** — with a real `trader` it only logs `"MM quote: ..."` and returns `None` (market_maker.py:248-252). Any reward-farming/MM plan that needs to actually rest orders must implement real placement, not assume it exists. (S6)
- **No UMA/oracle/dispute data is read anywhere** — full-codebase grep for `uma`/`dispute`/`umaResolutionStatus`/`acceptingOrders` returns nothing. S5 is greenfield data acquisition; `condition_id` is the only available join key. (S5)
- **Polymarket V2 / `py-clob-client` risk (out of scope here but blocking)** — repo pins `py-clob-client==0.34.5` (v1) and references USDC; research indicates a breaking V2 cutover (pUSD, new contracts) ~Apr 2026. **Verify against `docs.polymarket.com/changelog` before relying on any Polymarket execution plan (S1, S4, S5).** This is logged separately as an urgent verification item.
