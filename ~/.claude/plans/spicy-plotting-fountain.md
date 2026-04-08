# Plan: Fill Test Gaps

## Context
The test suite has 1,383 passing tests across 45 files, covering nearly all modules. Two specific revalidation methods in `executor.py` lack tests (`_revalidate_negrisk` and `_revalidate_kalshi_multi`), and two new untracked test files (`test_kalshi_scan.py`, `test_multi_cross.py`) plus `scans/multi_cross.py` need to be committed.

## Tasks

### 1. Add `_revalidate_negrisk` tests to `tests/test_executor.py`

Add a new `TestRevalidateNegRisk` class (after `TestRevalidateTriangular`) with these test cases:
- **passes when profit stays above threshold** — mock `fetch_order_book` to return asks, mock `net_profit_negrisk_internal` to return profit >= 90% of original
- **fails when no token_ids** — empty `_token_ids` returns False
- **fails when order book empty** — `fetch_order_book` returns None
- **fails when profit degrades** — new profit < 90% of original
- **uses WS cache when available** — price_cache hit skips `fetch_order_book`

Pattern: Follow `TestRevalidateTriangular` (line ~1250) which is the most recent revalidation test class.

Key functions to mock:
- `scanner.fetch_order_book` (patched via scanner facade)
- `scanner.get_best_bid_ask`
- `scanner.net_profit_negrisk_internal`

### 2. Add `_revalidate_kalshi_multi` tests to `tests/test_executor.py`

Add a new `TestRevalidateKalshiMulti` class with:
- **passes when profit stays above threshold** — mock `kalshi_client.fetch_order_book` for each ticker, mock `net_profit_kalshi_multi`
- **fails when no tickers** — empty `_kalshi_tickers` returns False
- **fails when no kalshi_client** — `self.kalshi_client` is None
- **fails when order book missing** — one ticker returns None
- **fails when profit degrades** — new profit < threshold

Key: Uses `self.kalshi_client.fetch_order_book` (not the scanner facade), so mock on the executor's kalshi_client attribute.

### 3. Commit untracked files

Stage and commit these files that are ready:
- `scans/multi_cross.py` — multi-outcome cross-platform scan module
- `tests/test_kalshi_scan.py` — Kalshi scan tests (188 lines)
- `tests/test_multi_cross.py` — multi-cross scan + executor integration tests (331 lines)
- `.env.example` — environment variable documentation

## Files to modify
- `tests/test_executor.py` — add ~80-100 lines (2 new test classes)

## Files to commit (untracked)
- `scans/multi_cross.py`
- `tests/test_kalshi_scan.py`
- `tests/test_multi_cross.py`
- `.env.example`

## Verification
1. `pytest tests/test_executor.py -v` — confirm new tests pass
2. `pytest tests/ -v` — confirm full suite still passes (1,383+ tests)
