# Plan 05 — UMA oracle dispute-risk gate (defensive)

**Strategy class:** Defensive risk control (not a profit center).
**Effort:** Medium (greenfield data acquisition).
**Flag:** `DISPUTE_GATE_ENABLED` (default `false`).
**Build before scaling any resolution-held arb** (Plans 01, 02, 03, and the existing `ResolutionSnipeOpp` / `SettlementTimingArb`).

## Why this exists

Every "risk-free" Polymarket arb that **holds to resolution** assumes a clean $1.00 settlement. Polymarket resolves via UMA's Optimistic Oracle, and in 2026 there have been **1,150+ disputed markets**. A dispute: (a) **freezes capital 4–6 days** while it escalates to the DVM token vote, and (b) can, rarely, **resolve the "certain" outcome the wrong way**. This gate refuses (or penalizes) new resolution-held positions on markets that are in a proposal/dispute window.

**Verified gap:** a full-codebase grep for `uma`/`dispute`/`umaResolutionStatus`/`acceptingOrders` returns **zero hits** — there is no oracle-state data anywhere today. `condition_id` is captured on every Polymarket market but used only as a dedup/match key. So this is net-new data acquisition.

## Design

Three pieces: **(A)** a fetch/parse path for dispute state, **(B)** a cache in `trades.db`, **(C)** a gate in `RiskManager.check()`.

`RiskManager.check()` already returns `(allowed, reason)` and never raises, and already receives the `db` object — so the gate is a clean insertion with **no signature change** and free `risk_rejections` metrics.

### Data source (phased)

- **Phase 1 (cheap, ship first):** parse the resolution-status fields the Gamma `/markets` response already carries. Gamma exposes `umaResolutionStatus` (e.g. `initialized` / `proposed` / `disputed` / `resolved`), `acceptingOrders`, and `closed`/`endDate`. `polymarket_api.fetch_all_markets` returns raw Gamma dicts unmodified, so these fields are already in hand — nothing parses them yet. Treat `umaResolutionStatus ∈ {proposed, disputed}` OR (`closed == True` AND not `resolved`) as **in the dispute/settlement window → block**.
- **Phase 2 (precise, later):** query the UMA Optimistic Oracle V2 contract (or Polymarket CTF adapter) on-chain for proposal timestamp + dispute flag. Requires the web3 layer built in Plan 04 — reuse it. Defer until Phase 1 proves the gate's value.

## Files to touch

| File | Change |
|------|--------|
| `uma_monitor.py` (new) | `fetch_dispute_states(markets) -> dict[condition_id, dict]` (Phase 1: parse Gamma fields) |
| `db.py` | `dispute_state` table + `upsert_dispute_state()` + `get_dispute_state(condition_id)` |
| `risk_manager.py` | constructor flag + gate #8 in `check()` |
| `cli.py` / `continuous.py` | refresh the dispute cache before the execution pass |
| `config.py`, tests, docs | flag + cover |

---

## Task 1 — `uma_monitor.py` (new, Phase 1)

```python
"""UMA / Polymarket resolution-state monitor (dispute-risk gate data source)."""
import logging
logger = logging.getLogger(__name__)

_BLOCKING_UMA_STATUSES = {"proposed", "disputed"}

def classify_dispute_state(market: dict) -> dict:
    """Return {'condition_id', 'state', 'blocked', 'reason'} for a Gamma market dict."""
    cid = market.get("conditionId") or market.get("condition_id") or ""
    status = (market.get("umaResolutionStatus") or "").lower()
    closed = bool(market.get("closed"))
    resolved = bool(market.get("resolved") or market.get("umaResolutionStatus") == "resolved")
    accepting = market.get("acceptingOrders", True)

    blocked, reason = False, "clear"
    if status in _BLOCKING_UMA_STATUSES:
        blocked, reason = True, f"uma_{status}"
    elif closed and not resolved:
        blocked, reason = True, "closed_unresolved"
    elif accepting is False:
        blocked, reason = True, "not_accepting_orders"
    return {"condition_id": cid, "state": status or ("closed" if closed else "open"),
            "blocked": blocked, "reason": reason}

def fetch_dispute_states(markets: list[dict]) -> dict[str, dict]:
    """Map condition_id -> classification for all markets carrying one."""
    out = {}
    for m in markets:
        c = classify_dispute_state(m)
        if c["condition_id"]:
            out[c["condition_id"]] = c
    return out
```

## Task 2 — `db.py`: cache table

Add to `TradeDB._create_tables()` (idempotent `CREATE TABLE IF NOT EXISTS`):

```sql
CREATE TABLE IF NOT EXISTS dispute_state (
    condition_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    blocked INTEGER NOT NULL,
    reason TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Add methods (thread-locked + WAL like the rest of `TradeDB`):

```python
def upsert_dispute_state(self, states: dict) -> int: ...      # INSERT OR REPLACE all rows
def get_dispute_state(self, condition_id: str) -> dict | None: ...   # row or None
```

## Task 3 — `risk_manager.py`: gate #8

Constructor (after the existing `config.get(...)` block):

```python
        self.dispute_gate_enabled = config.get("dispute_gate_enabled", False)
```

Add a class-level set of opp types the gate applies to (resolution-held only). Opp type strings are parametric (`NegRiskNO(4)`), so matching strips the `(N)` suffix and then requires exact membership — no prefix matching:

```python
    _DISPUTE_GATED_TYPES = frozenset({
        "Binary", "NegRisk", "NegRiskNO", "FrechetArb", "TemporalArb",
        "ResolutionSnipeOpp", "SettlementTimingArb",
    })
```

Insert as gate #8 in `check()`, immediately before `return True, "OK"`:

```python
        # 8. UMA dispute gate — block resolution-held Polymarket arbs on disputed markets
        if self.dispute_gate_enabled and opp_type.split("(")[0] in self._DISPUTE_GATED_TYPES:
            cid = opportunity.get("_condition_id") or opportunity.get("_market_key", "")
            ds = db.get_dispute_state(cid) if cid else None
            if ds and ds["blocked"]:
                return False, f"UMA dispute window ({ds['reason']})"
```

This requires scans to attach `_condition_id` (the Polymarket `conditionId`) to their opp dicts — most already carry it as `_market_key`; add an explicit `_condition_id` in the NegRisk/NegRiskNO/Frechet/Temporal emitters for robustness.

## Task 4 — refresh the cache

In `cli.py:_run_oneshot` (and `continuous.py`'s loop), after fetching `poly_markets` and **before** the execution pass, when `DISPUTE_GATE_ENABLED`:

```python
        if DISPUTE_GATE_ENABLED:
            from uma_monitor import fetch_dispute_states
            db.upsert_dispute_state(fetch_dispute_states(poly_markets))
```

Pass `dispute_gate_enabled=DISPUTE_GATE_ENABLED` into the `RiskManager` config dict where it's constructed.

## Task 5 — `config.py`

```python
DISPUTE_GATE_ENABLED = _env_bool("DISPUTE_GATE_ENABLED", "false")
```

## Task 6 — Tests

- `tests/test_uma_monitor.py` — `classify_dispute_state` for each case: `umaResolutionStatus="disputed"` → blocked; `"proposed"` → blocked; `closed=True, resolved=False` → blocked; clean open market → not blocked.
- `tests/test_risk_manager.py::TestDisputeGate` — with `dispute_gate_enabled=True` and a `db.get_dispute_state` mock returning `{"blocked": True, "reason": "uma_disputed"}`, `check()` on a `NegRiskNO(3)` opp returns `(False, "UMA dispute window (uma_disputed)")`; with gate disabled, passes; non-gated type (e.g. `BetfairBackAll`) passes regardless.

## Verification

```bash
pytest tests/test_uma_monitor.py tests/test_risk_manager.py -k Dispute -v
DISPUTE_GATE_ENABLED=true python scanner.py --mode negrisk-no --dry-run   # gate active, still detects
pytest tests/ -q
```

## Done criteria

- Phase-1 Gamma-field dispute classification populates a `dispute_state` cache in `trades.db`.
- `RiskManager` blocks resolution-held Polymarket opp types on disputed/proposal/closed-unresolved markets, gated by `DISPUTE_GATE_ENABLED`, emitting a `risk:UMA dispute window (...)` skip with metrics.
- Off by default; non-Polymarket and non-resolution-held strategies unaffected.
- Phase-2 backlog: on-chain UMA OO V2 query (reuse Plan 04's web3 layer) for proposal-timestamp precision and a dispute-discount *offensive* read (explicitly out of automated scope — politicized DVM votes).
