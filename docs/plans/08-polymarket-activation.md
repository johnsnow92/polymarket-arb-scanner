# Plan 08 — Polymarket Activation (CLOB V2, shadow-only)

**Status:** SPEC — build authorized for shadow mode only
**Authority:** Warroom decision W5 (2026-07-07, `24-STRATEGY-WARROOM-2026-07-07.md` §6) — Polymarket
execution ENABLED from Michigan (non-sports only), contingent on operator API-level verification.
**Queue position:** W7 lane #1 (2026-07-07 priority queue, item #2 spec / item #4 build).
**Written:** 2026-07-09.

---

## 1. Context and constraints

Polymarket became accessible from Michigan ~mid-June 2026 (non-sports; sports remains blocked
statewide). The 6/12 "read-only from MI" posture is superseded by W5. However:

- **App access ≠ API access.** API-level verification from the operator's account is an open
  [OP] item (queue #1). Until it passes, everything in this plan runs **shadow-only**
  (`DRY_RUN=true`, `polymarket` may remain in `ENABLED_EXECUTION_PLATFORMS` for dry-run paths
  but no live flip).
- **Policy broker precondition.** No agent-executed live order until broker PR #78 merges and its
  reconciliation passes tests (loop rule; doc 22). Live sizing/caps come from the broker's
  out-of-repo config — this plan does NOT define capital limits.
- **Money-authority hard-stop.** Any diff that flips the allowlist, touches capital caps, gate
  thresholds, or order-placement paths gets a 1-tap operator merge, never auto-merge.
- **Sports filter.** Markets categorized `sports` are excluded from all Polymarket execution
  paths regardless of fee-model support (MI litigation posture).

## 2. What already exists (do not rebuild)

| Piece | State | Where |
|---|---|---|
| Category taker-fee model (crypto 7% / sports 3% / finance-politics-mentions-tech 4% / econ-culture-weather-other 5% / geopolitics 0%; `rate·C·P·(1−P)`) | **SHIPPED** (PR #47) | `fees.py:POLYMARKET_CATEGORY_TAKER_RATES`, `polymarket_taker_fee()` |
| Maker-rebate model (25% of taker fee, 20% crypto) | **SHIPPED** (PR #47) | `fees.py:polymarket_maker_rebate()` |
| CLOB V2 client migration WIP: `py-clob-client-v2==1.0.2`, `OrderPayload`/`OrderType` (GTC/FOK map), httpx proxy injection for CLOB writes, pUSD/USDC collateral balance, dollar→shares sizing (`_dollar_size_to_contracts`), order-id persistence (`_persist_order_id`), venue-aware fill finalization, hedger + Kalshi sizing updates, test updates | **UNMERGED WIP** — preserved 2026-07-09 on branch `wip/clob-v2-snapshot-2026-07-09` (snapshot of uncommitted work found on `fix/layer4-continuous-wiring`) | branch `wip/clob-v2-snapshot-2026-07-09` |
| Proxy support for geoblock (`POLYMARKET_PROXY_URL`) | shipped (V1-era); V2 needs the httpx injection from the WIP | `polymarket_api.py` |

The build task is therefore: **review, harden, and land the V2 WIP as its own PR series**, not a
greenfield migration.

## 3. Build phases

### Phase A — Land the CLOB V2 client migration (this repo, PR-sized)
1. Start from `wip/clob-v2-snapshot-2026-07-09`; rebase onto current `master`; split out anything
   unrelated to the migration (the snapshot was taken from a mixed working tree).
2. Scope: `polymarket_api.py` (V2 imports, `OrderPayload`/`OrderType`, httpx proxy injection,
   pUSD collateral in `get_balance()`), `requirements.txt` (`py-clob-client-v2==1.0.2`),
   sizing helpers in `executor.py` (`_dollar_size_to_contracts`, zero-share skip guards),
   order-id persistence to SQLite at venue-accept time, matching test updates.
3. **Review flag:** this touches order-placement paths → money-authority merge (operator 1-tap
   after CodeRabbit + Codex both clean).
4. Acceptance: full suite green ×2 fixed-seed; V1 client fully removed (V1 endpoints are dead);
   dry-run order construction round-trips against recorded fixtures.

### Phase B — Shadow mode (no live orders)
1. Run continuous mode with `DRY_RUN=true` and Polymarket scans enabled; every would-be order is
   priced with the category fee model and logged (opportunity, legs, fees, expected net) to
   `trades.db` shadow tables + snapshot recorder.
2. Divergence logging: for each shadow fill, record book price at decision time vs T+5s re-fetch;
   summarize slippage distribution in the P&L digest.
3. Exit criteria to request live flip [OP]: ≥1 week shadow, no crash/recovery incidents,
   shadow fee accounting matches `polymarket_taker_fee()` to the cent on sampled markets,
   API-level verification passed.

### Phase C — Settlement-divergence VETO gate (mandatory before any cross-venue pair goes live)
Per W5: every Polymarket↔other-venue pair must pass a resolution-criteria equivalence check
before execution. Implement as a hard gate in the cross-platform scan refinement stage:
- Pair resolution-source/rule text (Polymarket UMA/oracle text vs counterpart venue rules);
  mismatch or missing rule text → VETO (fail-closed), opportunity dropped and logged.
- Reuses the market-equivalence machinery in `market_discovery.py` + `matcher.py`; the LLM
  settlement-divergence gate (tracker backlog) is the eventual scorer — until it lands, the
  gate is conservative string/source matching with an explicit allowlist of manually-verified
  pairs.

### Phase D — Scale gate (operator-owned)
First 50 dual-venue round-trips: zero settlement-divergence losses AND net-positive after fees,
else halt and review. Tracked from `trades.db`; surfaced in the P&L digest. Tranche sizing and
the $2–3K funding step are [OP] via the policy broker.

## 4. Explicitly out of scope
- Allowlist flag flip to live, funding, and API verification — [OP].
- Polymarket Liquidity Rewards quoting rotation (W6) — separate plan after MM-pilot verdict (~7/17).
- Agent-analyst judgment lane (queue #3) — separate spec `NN-agent-analyst.md`.
- Any capital/caps/risk policy — lives in the command center + broker config, not this repo.

## 5. Test plan
- Unit: V2 order construction (type map, price/size rounding, tick-size), pUSD balance parsing,
  `_dollar_size_to_contracts` boundaries (0-share, exact-multiple, price→1 and →0 edges).
- Integration: dry-run executor path per strategy branch that can route to Polymarket
  (`tests/integration/test_executor_strategies.py` extensions from the WIP).
- VETO gate: matched-pair fixtures (equivalent, divergent, missing-rules) → only equivalent passes.
- Regression: full suite ×2 fixed-seed before every push (loop VERIFY rule).

## 6. Risks
- **V2 WIP provenance:** the snapshot came from an unattributed working tree; every hunk must be
  re-reviewed as if new (no assumed prior review).
- **Geoblock/proxy:** CLOB writes from MI depend on the httpx proxy injection; a silent bypass
  would send unproxied signed requests — add a startup assertion when `POLYMARKET_PROXY_URL` is
  set that the module-level client actually carries the proxy.
- **Fee drift:** category rates verified 2026-06-10; re-verify against docs.polymarket.com before
  the live flip and record the check date in `fees.py`.
