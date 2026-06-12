# Plan 06 — Delta-neutral reward farming on Limitless / Opinion

**Strategy class:** Layer 3, near-risk-free (delta-neutral) / +EV with inventory risk (naked).
**Effort:** Med–High — requires a **new venue client** + **real order placement** (the current MM order path is a stub).
**Flags:** `LIMITLESS_REWARDS_ENABLED`, `OPINION_REWARDS_ENABLED` (default `false`).
**Depends on:** an **EIP-712 signer** (shared with the SX Bet #6 fix and Plan 04) and a new on-chain venue integration.

## Mechanism

Extend the existing Polymarket/Kalshi reward farming (`scans/rewards.py`) to **Limitless** (Base CLOB) and **Opinion** (BNB CLOB), both of which run active maker-rebate + points/airdrop programs. Post resting limit orders inside the reward spread band to earn **daily USDC rebates + points (→ airdrop optionality)**. Make it **delta-neutral** by hedging any filled inventory on Polymarket/Kalshi for the same event (reuse `hedger.py` + the cross-platform matching you already have). The reward often exceeds the spread given up, so it's +EV before the airdrop, which is free optionality on top.

## Honest dependencies & risks (read first)

- **`QuoteManager.place_quote` is a STUB** — with a real `trader` it only logs `"MM quote: ..."` and returns `None` (market_maker.py:248-252). **Reward farming needs orders to actually rest on the book, so real order placement must be built** (in the new venue client and wired through `place_quote`). This is the load-bearing work, not the reward math.
- **New venue = new integration** — Limitless/Opinion are not among the 8 platforms. This plan includes building `limitless_api.py` (and/or `opinion_api.py`), which is also a platform-expansion item. Both use **EIP-712 order signing** — the same primitive needed to un-quarantine SX Bet (#6) and used in Plan 04. **Build the EIP-712 signer once; it unlocks SX Bet + Limitless + Opinion.**
- **US-person / ToS gate (blocking, non-technical)** — both venues are US-unregulated; "no geoblock" ≠ "legal for a Michigan operator." This is a **legal go/no-go the operator must clear before any live capital** (see `docs/PLATFORM-MATRIX.md` candidate notes). Ship behind the flag in dry-run regardless.
- **Airdrop value is speculative** — treat as free optionality; never size positions assuming it.
- **Inventory risk if a hedge fails** — `hedger.py` refuses a hedge if the loss exceeds `HEDGE_MAX_SPREAD_LOSS_PCT`; on refusal the position is naked. Cap per-market inventory hard.

## Files to touch

| File | Change |
|------|--------|
| `limitless_api.py` (new) | venue client mirroring `gemini_api.py` shape (auth, markets, order book, **buy+sell place_order**, balance), EIP-712 signed |
| `eip712_signer.py` (new, shared) | the reusable EIP-712 order signer (also for SX Bet #6, Plan 04) |
| `scans/rewards.py` | generic reward adapter → `scan_limitless_rewards` (mirror `scan_polymarket_rewards`) |
| `market_maker.py` | implement the real `QuoteManager.place_quote` order path via the venue client |
| `hedger.py` | `limitless_client` slot + `_hedge_limitless` + `_attempt_hedge` branch |
| `cli.py`, `config.py`, tests, docs | wire + flags + cover |

---

## Task 1 — `eip712_signer.py` (shared primitive, build first)

Extract a reusable EIP-712 order signer (the codebase already has `poly-eip712-structs` and `eth_account` installed). Surface:

```python
def sign_order(domain: dict, order_struct: dict, private_key: str) -> str:
    """Return the 0x… signature for an EIP-712 typed order. Used by SX Bet (#6),
    Limitless, Opinion. Domain/struct are venue-specific; signing is shared."""
```

This is the highest-leverage piece: it also closes the existing SX Bet #6 gap. Build + unit-test it standalone (known domain/struct → known signature fixture).

## Task 2 — `limitless_api.py` (new venue client)

Mirror `gemini_api.py` exactly (it's the closest reference: HMAC/auth, full buy+sell, USDC, circuit breaker, `_RateLimitError`, `authenticated` attribute the hedger checks). Minimum surface to plug into the reward/MM/hedge machinery:

```python
class LimitlessClient:
    def __init__(self): self.authenticated = False; self.session = requests.Session(); ...
    def login(self, api_key=None, ...) -> bool: ...            # sets self.authenticated
    def fetch_all_markets(self, ...) -> list[dict]: ...        # scan/matcher-consumable dicts
    def get_order_book(self, market_id, limit=50) -> dict|None: ...   # {"bids":[...], "asks":[...]}
    def place_order(self, market_id, side, outcome, quantity, price,
                    time_in_force="gtc") -> dict|None: ...      # EIP-712 signed; buy AND sell
    def cancel_order(self, order_id) -> bool: ...
    def get_balance(self) -> float|None: ...
    def get_reward_program(self, market_id) -> dict|None: ...   # spread band + reward rate
```

Use `eip712_signer.sign_order` inside `place_order`. Add module-level `_RateLimitError`, `tenacity` retry, and a `PlatformCircuitBreaker` per the house pattern.

## Task 3 — real order placement in `market_maker.py`

Implement the live path of `QuoteManager.place_quote(platform, market_key, side, price, size, trader=None)` (currently a stub at lines 248-252): when `trader` is a real venue client, call `trader.place_order(...)`, capture the returned order id, and record it in the in-memory book (`_orders`). Return the real order id (not `None`). Keep the dry-run path (`dry_*` ids) intact. This is required for *any* live MM/reward order, not just this plan — flag it as a shared fix.

## Task 4 — reward scan adapter

In `scans/rewards.py`, generalize the Polymarket reward detector into a venue-parametrized adapter and add:

```python
def scan_limitless_rewards(limitless_client, min_pool_usdc=10.0, price_cache=None) -> list[dict]:
    """Mirror scan_polymarket_rewards: pull markets with an active reward program,
    validate metadata, compute optimal quotes via _calculate_optimal_quotes, two-stage refine."""
```

Opp dict mirrors `PolymarketRewards` with `type="LimitlessRewards"`, `_layer=3`, `platform="limitless"`, plus `optimal_bid/ask/spread`, `reward_pool_usdc`, `_market_key`. (Note: the `reward_tracker` arg in the existing scans is currently dead — quote math lives in `_calculate_optimal_quotes`; reuse that.)

## Task 5 — delta-neutral hedging

`hedger.py`: add `limitless_client=None` to `PartialFillHedger.__init__`, a `_hedge_limitless(pf, fill_price, size, max_loss)` method (fetch best opposing price via `get_order_book`, refuse if loss > `HEDGE_MAX_SPREAD_LOSS_PCT`, else sell), and a routing branch in `_attempt_hedge`. Wire the venue into the MM `on_fill` → `hedge_inventory(...)` path so a Limitless fill is neutralized on Polymarket/Kalshi for the matched event (use `matcher.match_cross_platform` to find the hedge venue's market). The matched event mapping is the only genuinely new logic — the hedge plumbing already exists.

## Task 6 — `config.py` / `cli.py`

```python
LIMITLESS_API_KEY = os.getenv("LIMITLESS_API_KEY", "")
LIMITLESS_REWARDS_ENABLED = _env_bool("LIMITLESS_REWARDS_ENABLED", "false")
LIMITLESS_MAX_INVENTORY   = _env_float("LIMITLESS_MAX_INVENTORY", "200.0")
```

`validate_config()`: if `LIMITLESS_REWARDS_ENABLED` and `DRY_RUN=false`, require `LIMITLESS_API_KEY` and that `limitless` ∈ `ENABLED_EXECUTION_PLATFORMS` (mirror the SX Bet quarantine gate). `cli.py`: add `"limitless-rewards"` to argparse `choices`, dispatch following the existing `rewards` block (lines 794-821).

## Task 7 — Tests

- `tests/test_eip712_signer.py` — known domain+struct → fixed signature.
- `tests/test_limitless_api.py` — `place_order` builds a correctly-signed payload (mock the HTTP); `get_order_book` parses bids/asks; `authenticated` gating.
- `tests/test_rewards.py::TestLimitless` — `scan_limitless_rewards` emits a `LimitlessRewards` opp for a market with a valid reward program; none below `min_pool_usdc`.
- `tests/test_hedger.py::TestLimitlessHedge` — a Limitless fill routes to `_hedge_limitless` and places an opposing order on the matched Polymarket market; refuses when loss > max.

## Verification

```bash
pytest tests/test_eip712_signer.py tests/test_limitless_api.py tests/test_rewards.py -k Limitless -v
LIMITLESS_REWARDS_ENABLED=true DRY_RUN=true python scanner.py --mode limitless-rewards   # dry-run only
pytest tests/ -q
```

## Done criteria

- Reusable EIP-712 signer built + tested (also unblocks SX Bet #6).
- `limitless_api.py` can read markets/books and place **signed buy+sell** orders; `QuoteManager.place_quote` actually rests orders live.
- `scan_limitless_rewards` detects reward opportunities; fills hedge delta-neutral on Polymarket/Kalshi via `hedger.py`.
- Flags default off; live capital gated on the **operator's legal/ToS sign-off** for a Michigan US person on Limitless/Opinion (non-technical blocker, documented in `PLATFORM-MATRIX.md`).
- Backlog: replicate for Opinion (`opinion_api.py`, `opinion-clob-sdk` Python SDK exists); naked-mode reward farming only behind a separate explicit flag.
