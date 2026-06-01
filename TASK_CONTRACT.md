# Task Contract

> **Owner:** Jonathon Tamm · **Review cadence:** when core data shapes or the definition of done change.
> Per the global operating rule, when this file exists it is the authority on "done."

## Definition of done
A change is done when it: **compiles, tests pass, and behavior is verified** (run it / observe it — not just unit asserts). No stubs, no TODO-as-shipped, no silently swallowed errors. Doc-only changes are done when they match code reality (see `docs/audit/REGISTER-2026-05-31.md`).

## Opportunity dict schema
Opportunities flow through the system as plain dicts. Standard keys:

| Key | Meaning |
|---|---|
| `type` | Opportunity type string — drives `executor.py:_build_legs()` dispatch (e.g. `MarketMake`, `EventDivergence`, `KalshiMulti`, `MultiCross`, `Imbalance`, `NewsSnipe`, `Correlated`, `TimeDecay`, `LogicalArb`, `WhaleCopy`, `PolymarketRewards`, `KalshiRewards`, cross/binary/negrisk variants) |
| `market` | Human-readable market label |
| `prices` | Price components for the legs |
| `total_cost` | Total entry cost (often a `"$0.9500"` string) |
| `net_profit` | Net profit after fees |
| `net_roi` | ROI after fees |
| `_token_ids` | CLOB token IDs extracted during scan (`_extract_token_ids`) |
| `_clob_depth` | Order-book depth captured at refinement |
| `_market_key` | Dedup / per-market-lock key |

Internal keys are `_`-prefixed. New types **must** add a `_build_legs()` branch **and** a matching `_revalidate` case (see `CONTRIBUTING.md`). A type is only labeled tradable/BUILT when both exist plus per-market-lock coverage.

## Execution legs
`_build_legs(opp)` converts an opportunity into platform-specific legs (platform, side, price, size, token/contract id). `ArbitrageExecutor.execute` then: risk gate → gas gate → revalidation (reject if profit dropped >10%) → sizing → order placement → fill confirmation → DB log.

## Custom exceptions
- `ConfigError(ValueError)` — `config.py:18`; raised throughout `validate_config()` for missing/invalid config.
- `_RateLimitError(Exception)` — defined per-client (`betfair_api.py:17`, `kalshi_api.py`, `polymarket_api.py`); drives `tenacity` retry/backoff and circuit-breaker logic.

No other custom exception classes exist; everything else uses stdlib/`requests` exceptions handled explicitly (never swallowed).
