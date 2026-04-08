# Add Gemini Predictions + IBKR ForecastEx Platforms

## Context

The 4 UK exchange platforms (Betfair, Smarkets, SX Bet, Matchbook) are inaccessible from the US. Gemini Predictions (CFTC DCM, all 50 states, launched Dec 2025) and IBKR ForecastEx (SEC-regulated, BUY-only) are US-accessible replacements. Both have $0 fees currently. This plan adds full platform integration for both, following existing codebase patterns.

**Gemini**: Full-featured — HMAC-SHA384 auth, limit orders (IOC/GTC), buy+sell, 0% fees (promo), prices 0-1, binary + categorical markets, order book via `/v1/book/{symbol}`, WebSocket available.

**IBKR**: Constrained — OAuth 2.0 via `ibauth`, BUY-only (no sell), LMT-only, close by buying opposing contract (auto-nets at IBKR), $0.00 commission, 10 req/sec. No back-lay scans, no hedging possible.

---

## Phase 1: API Clients

### 1a. Create `gemini_api.py` (~300 lines)

New file. `GeminiClient` class following `kalshi_api.py` pattern:

- **Auth**: HMAC-SHA384 signing — `X-GEMINI-APIKEY`, `X-GEMINI-PAYLOAD` (base64 JSON with `request` + `nonce`), `X-GEMINI-SIGNATURE` (hex HMAC of payload)
- **Base URL**: `https://api.gemini.com` (configurable via `GEMINI_BASE_URL`)
- **Rate limiting**: Thread-safe, 0.1s min interval (600 req/min private)
- **Retry**: `tenacity` for 429s (same as `kalshi_api.py`)

Methods:
```
login(api_key, api_secret) -> bool
fetch_all_markets(status="active", category=None) -> list[dict]
    # GET /v1/prediction-markets/events with pagination (limit=100)
    # Returns normalized list: [{id, title, type, category, contracts: [{id, label, price, instrumentSymbol, outcome}]}]
get_market_price(event) -> tuple[float|None, float|None]
    # For binary: extract YES/NO contract prices directly (0-1 already)
    # For categorical: return None,None (handled by multi scan)
get_order_book(instrument_symbol, limit=50) -> dict
    # GET /v1/book/{instrumentSymbol} -> {bids: [{price, amount}], asks: [{price, amount}]}
place_order(symbol, side, outcome, quantity, price, time_in_force="immediate-or-cancel") -> dict|None
    # POST /v1/prediction-markets/order
get_order_status(order_id) -> dict|None
    # POST /v1/prediction-markets/orders/active + filter by ID
    # (or use /v1/prediction-markets/orders/history for filled)
cancel_order(order_id) -> bool
    # POST /v1/prediction-markets/order/cancel
get_balance() -> float|None
    # POST /v1/balances -> filter currency=="USD", return available
get_positions() -> list[dict]
    # POST /v1/prediction-markets/positions
get_market_status(event_ticker) -> dict|None
    # GET /v1/prediction-markets/events/{eventTicker} -> check status field
```

### 1b. Create `ibkr_api.py` (~250 lines)

New file. `IBKRClient` class:

- **Auth**: OAuth 2.0 via `ibauth` library. Store access/refresh tokens, auto-refresh.
- **Base URL**: `https://api.ibkr.com/v1/api` (configurable via `IBKR_BASE_URL`)
- **Rate limiting**: Thread-safe, 0.1s min interval (10 req/sec global), 5s interval for orders
- **Key constraint**: BUY-only. No SELL orders. Close = buy opposing contract (auto-nets).

Methods:
```
login(client_id, client_secret) -> bool
    # OAuth 2.0 flow via ibauth — get access_token
fetch_all_markets() -> list[dict]
    # GET /forecastex/contracts with pagination
    # Normalize to: [{id, title, contracts: [{conid, label, side, price}]}]
get_market_price(market) -> tuple[float|None, float|None]
    # GET /md/snapshot?conids={yes_conid},{no_conid}
    # Extract best ask prices, normalize to 0-1 (IBKR uses 0-100 cents)
place_order(conid, quantity, price) -> dict|None
    # POST /orders — BUY LMT only, amount in cents (0-100)
get_order_status(order_id) -> dict|None
    # GET /orders/{orderId}
cancel_order(order_id) -> bool
    # DELETE /orders/{orderId}
get_balance() -> float|None
    # GET /portfolio/accounts -> available funds
get_market_status(contract_id) -> dict|None
    # GET /forecastex/contracts/{contract_id} -> status field
```

### 1c. Tests: `tests/test_gemini_api.py`, `tests/test_ibkr_api.py` (~100 lines each)

- Test auth header signing (Gemini HMAC, IBKR OAuth token attach)
- Test `fetch_all_markets` with mocked paginated responses
- Test `place_order` success/failure
- Test rate limiting behavior
- Test IBKR BUY-only constraint (no SELL parameter)

---

## Phase 2: Fee Calculations

### 2a. Add Gemini fees to `fees.py`

```python
# Gemini: 0% fees during promotional period (configurable for future)
def net_profit_gemini_binary(yes_price, no_price, fee_rate=0.0) -> dict
def net_profit_gemini_multi(yes_prices, fee_rate=0.0) -> dict
def net_profit_cross_gemini(poly_price, gm_price, poly_side, gm_side, fee_rate=0.0) -> dict
```

### 2b. Add IBKR fees to `fees.py`

```python
# IBKR ForecastEx: $0.00 commission
def net_profit_ibkr_binary(yes_price, no_price) -> dict
    # BUY YES + BUY NO (both are buy orders). One pays $1, other $0.
def net_profit_cross_ibkr(poly_price, ibkr_price, poly_side, ibkr_side) -> dict
```

### 2c. Update `_platform_win_fee` and `_platform_entry_fee` for triangular support

Add `"gemini"` and `"ibkr"` branches (both return 0.0 for now).

### 2d. Add to cross-platform fee lookup in `scans/cross.py`

Add to `_CROSS_FEE_FUNCS`:
```python
("polymarket", "gemini"): net_profit_cross_gemini,
("polymarket", "ibkr"): net_profit_cross_ibkr,
```

### 2e. Tests in existing `tests/test_fees.py` (~60 lines)

- Test all new fee functions with zero-fee and configurable fee scenarios

---

## Phase 3: Scan Modules

### 3a. Create `scans/gemini.py` (~200 lines)

Two scan functions following `scans/matchbook.py` pattern:

```python
def scan_gemini_binary(client, min_profit) -> list[dict]
    # Fetch all binary events
    # For each: get YES + NO prices, call net_profit_gemini_binary
    # Opportunity dict keys: type="GeminiBinary", _gm_event_id, _gm_yes_symbol, _gm_no_symbol

def scan_gemini_multi(client, min_profit) -> list[dict]
    # Fetch all categorical events
    # For each: get all contract prices, call net_profit_gemini_multi
    # Opportunity dict keys: type="GeminiMulti", _gm_event_id, _gm_symbols (list), _gm_prices (list)
```

Note: Gemini has full order book access so we can do CLOB refinement using `/v1/book/{symbol}` ask prices.

### 3b. Create `scans/ibkr.py` (~120 lines)

One scan function (BUY-only = binary internal arbs only, no back-lay):

```python
def scan_ibkr_binary(client, min_profit) -> list[dict]
    # Fetch all ForecastEx contracts
    # For each binary event: BUY YES + BUY NO (both buys!)
    # Call net_profit_ibkr_binary
    # Opportunity dict keys: type="IBKRBinary", _ibkr_event_id, _ibkr_yes_conid, _ibkr_no_conid
```

No back-lay or multi-outcome scan — IBKR only supports BUY, no laying/selling.

### 3c. Update `scans/__init__.py`

Add exports: `scan_gemini_binary`, `scan_gemini_multi`, `scan_ibkr_binary`

### 3d. Update `scanner.py` facade

Add re-exports for the 3 new scan functions.

### 3e. Tests: `tests/test_gemini_scan.py`, `tests/test_ibkr_scan.py` (~100 lines each)

- Test binary scan finds profitable under-round
- Test multi scan (Gemini only) finds categorical arbs
- Test no false positives when sum >= 1.0
- Test empty markets / unauthenticated client

---

## Phase 4: Config & CLI Integration

### 4a. Add env vars to `config.py`

```python
# Gemini Predictions
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_SECRET = os.getenv("GEMINI_API_SECRET")
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://api.gemini.com")
GEMINI_FEE_RATE = float(os.getenv("GEMINI_FEE_RATE", "0.0"))
GEMINI_RATE_LIMIT = float(os.getenv("GEMINI_RATE_LIMIT", "0.1"))

# IBKR ForecastEx
IBKR_CLIENT_ID = os.getenv("IBKR_CLIENT_ID")
IBKR_CLIENT_SECRET = os.getenv("IBKR_CLIENT_SECRET")
IBKR_BASE_URL = os.getenv("IBKR_BASE_URL", "https://api.ibkr.com/v1/api")
IBKR_RATE_LIMIT = float(os.getenv("IBKR_RATE_LIMIT", "0.1"))
IBKR_ORDER_RATE_LIMIT = float(os.getenv("IBKR_ORDER_RATE_LIMIT", "5.0"))
```

### 4b. Update `cli.py`

1. **Add modes** to argparse choices: `"gemini"`, `"ibkr"`
2. **Import** new scan functions and client classes
3. **Initialize clients** (same pattern as matchbook):
   ```python
   gemini_client = None
   if args.mode in ("all", "cross-all", "gemini"):
       gm_key = os.getenv("GEMINI_API_KEY")
       gm_secret = os.getenv("GEMINI_API_SECRET")
       if gm_key and gm_secret:
           gemini_client = GeminiClient()
           if not gemini_client.login(gm_key, gm_secret):
               gemini_client = None

   ibkr_client = None
   if args.mode in ("all", "cross-all", "ibkr"):
       ibkr_id = os.getenv("IBKR_CLIENT_ID")
       ibkr_secret = os.getenv("IBKR_CLIENT_SECRET")
       if ibkr_id and ibkr_secret:
           ibkr_client = IBKRClient()
           if not ibkr_client.login(ibkr_id, ibkr_secret):
               ibkr_client = None
   ```
4. **Dispatch scans** in `_run_oneshot`:
   ```python
   if args.mode in ("all", "gemini"):
       # scan_gemini_binary + scan_gemini_multi

   if args.mode in ("all", "ibkr"):
       # scan_ibkr_binary
   ```
5. **Add to executor init** and `extra_clients` dict
6. **Add to cross-all** platform list for cross-platform scanning

---

## Phase 5: Executor Integration

### 5a. Constructor — add `gemini_client` and `ibkr_client` params

File: `executor.py` constructor (lines 33-52, stored as instance vars lines 53-69)

### 5b. `_build_legs()` — add GeminiBinary, GeminiMulti, IBKRBinary, CrossPlatform with gemini/ibkr

```python
elif opp_type == "GeminiBinary":
    legs = [
        {"platform": "gemini", "symbol": opp["_gm_yes_symbol"], "side": "buy",
         "outcome": "yes", "price": ..., "size": size},
        {"platform": "gemini", "symbol": opp["_gm_no_symbol"], "side": "buy",
         "outcome": "no", "price": ..., "size": size},
    ]

elif opp_type == "GeminiMulti":
    for symbol, price in zip(opp["_gm_symbols"], opp["_gm_prices"]):
        legs.append({"platform": "gemini", "symbol": symbol, "side": "buy",
                      "outcome": "yes", "price": price, "size": size})

elif opp_type == "IBKRBinary":
    legs = [
        {"platform": "ibkr", "conid": opp["_ibkr_yes_conid"], "side": "buy",
         "price": ..., "size": size},
        {"platform": "ibkr", "conid": opp["_ibkr_no_conid"], "side": "buy",
         "price": ..., "size": size},
    ]
```

### 5c. `_execute_single_leg()` — add gemini and ibkr dispatch

```python
elif platform == "gemini":
    resp = self.gemini_client.place_order(symbol, "buy", outcome, size, price, "immediate-or-cancel")
    order_id = resp.get("orderId") if resp else None
    return True, order_id, self._confirm_fill_gemini(order_id, price)

elif platform == "ibkr":
    resp = self.ibkr_client.place_order(conid, size, price)
    order_id = resp.get("orderId") if resp else None
    return True, order_id, self._confirm_fill_ibkr(order_id, price)
```

### 5d. Fill confirmation — add `_confirm_fill_gemini` and `_confirm_fill_ibkr`

Same poll pattern as existing platforms. Gemini: check `status=="filled"`. IBKR: check order status for `Filled`.

### 5e. `_cancel_leg()` — add gemini and ibkr branches

### 5f. `_revalidate()` (line 146) + `_refetch_platform_price()` (line 464)

Add gemini and ibkr branches. Gemini: re-fetch order book ask prices. IBKR: re-fetch snapshot prices.

### 5g. `_per_leg_budget()` — add gemini and ibkr balance routing

### 5h. `_fetch_balances()` — add gemini and ibkr balance fetching

### 5i. `_build_event_divergence_legs()` — add gemini and ibkr

Gemini: full buy/sell. IBKR: BUY_YES = buy YES conid, BUY_NO = buy NO conid (both are BUY).

---

## Phase 6: Hedger, Recovery, Continuous Mode

### 6a. `hedger.py` — add `gemini_client` to constructor + `_hedge_gemini()`

Gemini supports SELL, so hedging works: sell the filled leg at market (IOC at worst ask).

**IBKR: No hedger support** — cannot sell. Document this as a known limitation. If a partial fill occurs on an IBKR cross-platform arb, the IBKR leg cannot be hedged (auto-netting only works for same-event opposing contracts).

### 6b. `recovery.py` — add Gemini + IBKR to `reconcile_orphaned_positions()`

Check open positions/orders on startup and reconcile.

### 6c. `continuous.py` — extend `_extract_keys()` for gemini and ibkr opportunities

```python
if opp.get("_gm_event_id"):
    keys.append(("gemini", opp["_gm_event_id"]))
if opp.get("_ibkr_event_id"):
    keys.append(("ibkr", opp["_ibkr_event_id"]))
```

### 6d. Tests (~80 lines across existing test files)

- Test hedger with gemini_client
- Test hedger skips IBKR (no sell capability)
- Test continuous mode key extraction for both platforms

---

## Phase 7: Cross-Platform & Triangular

### 7a. `matcher.py` — add Gemini + IBKR title matching

Add normalization for Gemini event titles (strip `GEMI-` prefixes) and IBKR contract names to match against Polymarket/Kalshi slugs.

### 7b. `scans/cross.py` — add gemini and ibkr to all-platform scanning

The `scan_cross_all` function iterates platform pairs. Add gemini and ibkr to the platform list with their respective `fetch_all_markets` + `get_market_price` functions.

### 7c. `scans/triangular.py` — add gemini and ibkr to triangular candidate pool

These platforms join the union-find grouping for 3-way arb detection. Also update `_attach_exec_metadata()` (line 136) to add gemini/ibkr-specific fields (event ID, instrument symbols/conids).

### 7d. Tests (~60 lines)

- Test cross-platform fee calculation for PM-Gemini and PM-IBKR pairs
- Test triangular scan includes gemini/ibkr platforms

---

## Files Summary

| File | Action | Changes |
|------|--------|---------|
| `gemini_api.py` | **CREATE** | GeminiClient class (~300 lines) |
| `ibkr_api.py` | **CREATE** | IBKRClient class (~250 lines) |
| `scans/gemini.py` | **CREATE** | scan_gemini_binary, scan_gemini_multi (~200 lines) |
| `scans/ibkr.py` | **CREATE** | scan_ibkr_binary (~120 lines) |
| `tests/test_gemini_api.py` | **CREATE** | Client tests (~100 lines) |
| `tests/test_ibkr_api.py` | **CREATE** | Client tests (~100 lines) |
| `tests/test_gemini_scan.py` | **CREATE** | Scan tests (~100 lines) |
| `tests/test_ibkr_scan.py` | **CREATE** | Scan tests (~100 lines) |
| `config.py` | MODIFY | Add 10 env vars for both platforms |
| `fees.py` | MODIFY | Add 5 fee functions + update triangular helpers |
| `scans/__init__.py` | MODIFY | Export 3 new scan functions |
| `scans/cross.py` | MODIFY | Add 2 cross-fee lookup entries, add to platform list |
| `scans/triangular.py` | MODIFY | Add gemini/ibkr to candidate pool |
| `scanner.py` | MODIFY | Re-export 3 new scan functions |
| `cli.py` | MODIFY | Add modes, imports, client init, scan dispatch |
| `executor.py` | MODIFY | Constructor, _build_legs, _execute_single_leg, fill confirm, cancel, revalidate, budget, balances, event divergence |
| `hedger.py` | MODIFY | Add gemini_client (not IBKR — can't sell) |
| `continuous.py` | MODIFY | OpportunityIndex key extraction |
| `recovery.py` | MODIFY | Add gemini + ibkr to reconciliation |
| `matcher.py` | MODIFY | Add Gemini/IBKR title normalization |
| `tests/test_fees.py` | MODIFY | Add tests for new fee functions (~60 lines) |
| `tests/test_executor.py` | MODIFY | Add tests for new platform legs (~80 lines) |
| `tests/test_continuous.py` | MODIFY | Add key extraction tests (~20 lines) |

---

## IBKR Constraints Handled

| Constraint | How Handled |
|---|---|
| BUY-only (no SELL) | Only internal binary arbs (buy YES + buy NO). No back-lay scan. |
| LMT-only | All orders use limit price (IOC equivalent via short expiry) |
| Close = buy opposing | Not needed for arbs (we already buy both sides) |
| No hedging | Skip IBKR in hedger. Cross-platform IBKR legs are unhedgeable. |
| 5s order rate limit | Separate `IBKR_ORDER_RATE_LIMIT` enforced in client |

---

## Verification

```bash
# All tests pass
pytest tests/ -v

# Platform-specific dry runs
python scanner.py --mode gemini --dry-run
python scanner.py --mode ibkr --dry-run

# Cross-platform includes new platforms
python scanner.py --mode cross-all --dry-run

# Full scan
python scanner.py --mode all --dry-run
```
