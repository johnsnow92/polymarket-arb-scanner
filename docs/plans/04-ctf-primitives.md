# Plan 04 — CTF mint/split & merge/redeem primitives

**Strategy class:** Layer 1. Merge direction = risk-free; mint-and-sell = near-risk-free (leg risk).
**Effort:** High — this is a **net-new on-chain write subsystem**, not an extension of existing write code.
**Flags:** `CTF_ENABLED`, `CTF_MERGE_ENABLED`, `CTF_MINT_SELL_ENABLED`, `CTF_CONVERT_ENABLED` (all default `false`).
**Unlocks:** the capital-efficient NegRisk *convert* (makes Plan 01 capital-light) and the **mint-and-sell** direction the bot cannot express today.

## Mechanism

Polymarket markets are Gnosis Conditional Token Framework (CTF) positions. Two primitives the bot never touches:

- **Mint-and-sell:** when `YES_bid + NO_bid > $1.00`, call `ConditionalTokens.splitPosition()` to mint 1 YES + 1 NO from $1 collateral (fee-free on the contract), then **sell both legs** into the order books for > $1. Profit = `(YES_bid + NO_bid) − 1 − gas − sell_fees`. *Cannot be expressed by a buy-only executor — this is genuinely new capability.*
- **Buy-and-merge:** when `YES_ask + NO_ask < $1.00`, buy both legs and call `mergePositions()` to reconstitute $1 **immediately** (no waiting for resolution). Same edge as the existing `Binary` arb but **without resolution lockup**.
- **Redeem:** `redeemPositions()` after settlement (locks capital through the dispute window → gate with Plan 05; prefer merge).
- **NegRisk convert:** `NegRiskAdapter.convertPositions()` burns a NO-set and returns collateral + the complement YES — the capital-efficient version of Plan 01 (recover capital immediately instead of holding to resolution). On-chain data shows convert/rebalance was the largest share of the documented NegRisk profit pool.

## Honest status: what exists vs what's missing (verified)

**EXISTS:** `eth_account==0.13.7`, `eth_abi==5.2.0`, `eth_utils==6.0.0` (transitive via py-clob-client, importable today); a proven ABI codec reference in `whale_copy_decoder.py` (`eth_abi.decode`, `eth_utils.keccak`, selector helper); `POLYGON_RPC_URL` config + a raw JSON-RPC POST path in `gas_monitor`; the `treasury.web3_send_usdc` injection *shape* (`fn(amount, dest) -> tx_hash`); the executor's per-platform client-injection + leg-dispatch switch; the `db.transfers` table with a `tx_hash` column.

**MISSING (the whole transaction layer):** `web3.py` is **not installed and not in requirements**; there is **no raw-transaction signing/broadcast code anywhere** (the private key is only ever handed to the CLOB SDK for *order* signing); **no ConditionalTokens / NegRiskAdapter / USDC contract addresses or ABIs**; **no CTF client module**; **no executor branch** for contract-call legs; **no gas model** for ERC-1155 calls (hardcoded 21000 gas, `PLATFORM_GAS_TXNS["polymarket"]=1`); and opp dicts carry only `_token_ids` (CLOB IDs), **not** the `conditionId` / `indexSet` / `partition` data these calls require. `treasury.web3_send_usdc` is a `None`-defaulted hole never passed by its only constructor — there is **no working on-chain write to copy**.

**Bottom line:** build the transaction subsystem first; the strategies are thin on top of it.

## Risk caveats

- **Merge = risk-free** (atomic on-chain reconstitution). **Mint-and-sell = leg risk** (mint is atomic, but the two sells hit the book sequentially — the second leg can move). Mitigate with the existing `hedger.py`. Classify mint-sell as near-risk-free.
- **Redeem locks capital through the UMA dispute window** → require Plan 05's gate; prefer merge over redeem.
- **Gas + approvals** — split/merge/redeem are real Polygon txs (ERC-1155, materially > 21000 gas); USDC and CTF need one-time `approve()`. Model actual gas before sizing.
- **Key handling** — this introduces **raw transaction signing** with `POLYMARKET_PRIVATE_KEY` (today it only signs CLOB orders). Treat as custody-grade (see `SECURITY.md`); never log the key; testnet first.
- **Polymarket V2 risk** — confirm contract addresses against the current (post-V2) deployment before wiring; collateral may be pUSD, not USDC.e (see README gotcha).

## Phasing (do not build it all at once)

- **4a — read-only + dry-run detection.** Add `web3.py`, contract addresses/ABIs, a `ctf_api.py` that can *read* balances/positions and *simulate* (build calldata, `eth_call`, estimate gas) but never broadcast. New `scans/ctf.py` detects mint-sell (`yes_bid+no_bid>1`) and merge (`yes_ask+no_ask<1`) opportunities, dry-run only. **Ship and run in dry-run for a week before 4b.**
- **4b — merge execution on Amoy testnet, then mainnet.** Implement signed `splitPosition`/`mergePositions` broadcast + receipt polling. Prove on Polygon Amoy with throwaway funds. Merge first (risk-free, no leg risk).
- **4c — mint-and-sell + NegRisk convert.** Add the mint→sell→hedge flow and `convertPositions` (wires back into Plan 01 as the capital-efficient path).

## Files to touch

| File | Change |
|------|--------|
| `requirements.txt` | add `web3` (pin a current 6.x/7.x) |
| `config.py` | CTF/NegRiskAdapter/ConditionalTokens/USDC **addresses** + `CTF_*` flags |
| `contracts/ctf_abis.py` (new) | minimal ABI fragments: `splitPosition`, `mergePositions`, `redeemPositions`, `convertPositions`, ERC-20 `approve`/`allowance` |
| `ctf_api.py` (new) | `CTFClient` — build/sign/send/poll, mirroring `whale_copy_decoder` codec + `treasury` injection shape |
| `scans/ctf.py` (new) | mint-sell + merge detection (two-stage) |
| `fees.py` | `net_profit_ctf_mint()`, `net_profit_ctf_merge()` (CTF gas model) |
| `gas_monitor.py` | CTF gas accounting (ERC-1155, not 21000) |
| `executor.py` | 9th client `ctf_client`; `polymarket_ctf` branch in `_build_legs` + `_execute_single_leg`; `_NO_CANCEL_PLATFORMS` membership; `_revalidate` pass-through |
| tests, docs | unit + testnet integration |

---

## Task 4a.1 — dependencies + addresses

`requirements.txt`: add `web3` (current major). Note `eth_account`/`eth_abi`/`eth_utils` are already present transitively but **pin them explicitly** now that they're load-bearing.

`config.py` — add (verify each against the current post-V2 deployment before use):

```python
# CTF / on-chain (Polygon). VERIFY against the live Polymarket deployment.
CONDITIONAL_TOKENS_ADDRESS = os.getenv("CONDITIONAL_TOKENS_ADDRESS", "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")
NEG_RISK_ADAPTER_ADDRESS   = os.getenv("NEG_RISK_ADAPTER_ADDRESS", "")   # fill from docs.polymarket.com
USDC_ADDRESS               = os.getenv("USDC_ADDRESS", "")               # collateral (USDC.e or pUSD post-V2)
CTF_ENABLED        = _env_bool("CTF_ENABLED", "false")
CTF_MERGE_ENABLED  = _env_bool("CTF_MERGE_ENABLED", "false")
CTF_MINT_SELL_ENABLED = _env_bool("CTF_MINT_SELL_ENABLED", "false")
CTF_CONVERT_ENABLED   = _env_bool("CTF_CONVERT_ENABLED", "false")
```

`validate_config()`: if any `CTF_*` flag is true while `DRY_RUN=false`, require the addresses to be non-empty and `web3` importable — else raise `ConfigError` (mirror the SX Bet quarantine pattern).

## Task 4a.2 — `ctf_api.py` (CTFClient)

Mirror the **injection shape** of `treasury.web3_send_usdc` and the **codec usage** of `whale_copy_decoder.py`. Minimum surface:

```python
class CTFClient:
    def __init__(self, rpc_url: str, private_key: str,
                 conditional_tokens: str, neg_risk_adapter: str, usdc: str,
                 dry_run: bool = True): ...
    # reads
    def collateral_balance(self) -> float: ...
    def position_balance(self, token_id: str) -> float: ...
    # build-only (4a): returns unsigned tx + estimated gas, never broadcasts
    def build_split(self, condition_id: str, amount_usdc: float) -> dict: ...
    def build_merge(self, condition_id: str, amount: float) -> dict: ...
    def build_redeem(self, condition_id: str, index_sets: list[int]) -> dict: ...
    def build_convert(self, condition_id: str, index_set: int, amount: float) -> dict: ...
    # 4b+: sign + broadcast + poll receipt; idempotent nonce mgmt
    def send(self, built_tx: dict) -> str | None: ...   # returns tx_hash
    def ensure_allowance(self, spender: str, min_amount: float) -> None: ...
```

`build_*` use `eth_abi.encode` + the function selectors for `splitPosition(collateral, parentCollectionId, conditionId, partition, amount)` etc. (binary market partition = `[1, 2]`). `send` does `Account.sign_transaction` + `eth_sendRawTransaction` + `wait_for_transaction_receipt`. **In 4a, `send` raises `NotImplementedError`** — detection runs build/estimate only.

## Task 4a.3 — `scans/ctf.py` + `fees.py`

`scan_ctf(markets, min_profit, price_cache)`:
- **mint-sell:** `yes_bid + no_bid > 1` → `net_profit_ctf_mint(yes_bid, no_bid)`; emit `type="CTFMint"`.
- **merge:** `yes_ask + no_ask < 1` → `net_profit_ctf_merge(yes_ask, no_ask)`; emit `type="CTFMerge"`.

Opp carries `_condition_id`, `_yes_token`, `_no_token`, `_amount`. Two-stage refine against live book + a gas check via `gas_monitor`.

`fees.py`:
```python
def net_profit_ctf_merge(yes_ask, no_ask):
    gross = 1.0 - (yes_ask + no_ask)
    if gross <= 0: return {"gross_spread": gross, "fees": 0, "net_profit": gross}
    fees = polymarket_taker_fee(yes_ask) + polymarket_taker_fee(no_ask)  # buy legs
    gas = CTF_GAS_ESTIMATE + POLYGON_GAS_ESTIMATE * 2                    # merge + 2 buys
    return {"gross_spread": gross, "fees": fees + gas, "net_profit": gross - fees - gas}

def net_profit_ctf_mint(yes_bid, no_bid):
    gross = (yes_bid + no_bid) - 1.0
    if gross <= 0: return {"gross_spread": gross, "fees": 0, "net_profit": gross}
    fees = polymarket_taker_fee(yes_bid) + polymarket_taker_fee(no_bid)  # sell legs
    gas = CTF_GAS_ESTIMATE + POLYGON_GAS_ESTIMATE * 2                    # split + 2 sells
    return {"gross_spread": gross, "fees": fees + gas, "net_profit": gross - fees - gas}
```

Add `CTF_GAS_ESTIMATE` to `config.py` (measure on Amoy; ERC-1155 split/merge ≫ a 21000-gas transfer).

## Task 4b — executor wiring

- `executor.__init__`: accept a 9th client `ctf_client=None`; store as `self.ctf_client`.
- `_build_legs`: new branches emitting a single contract-call leg:
  ```python
  elif opp_type == "CTFMerge":
      legs = [{"platform": "polymarket_ctf", "action": "merge",
               "condition_id": opportunity["_condition_id"], "amount": size,
               "_buy_legs": [...yes/no CLOB buys...]}]
  elif opp_type == "CTFMint":
      legs = [{"platform": "polymarket_ctf", "action": "split",
               "condition_id": opportunity["_condition_id"], "amount": size,
               "_sell_legs": [...yes/no CLOB sells...]}]
  ```
- `_execute_single_leg`: new `elif platform == "polymarket_ctf":` branch dispatching `action` to `self.ctf_client.send(self.ctf_client.build_*(...))`; returns `(success, tx_hash, None)`. Add `"polymarket_ctf"` to `_NO_CANCEL_PLATFORMS` (atomic, non-cancellable). For `CTFMerge`, execute the two CLOB buys first (existing path), then merge; for `CTFMint`, split first, then the two sells (hedge the second leg via `hedger.py`).
- `_revalidate`: `elif opp_type in ("CTFMerge", "CTFMint"): reason = "deterministic_ctf"` (no mid-price staleness — book is live, economics are deterministic), but re-check the live book one more time before the contract call.
- Record `tx_hash` via the existing `db` transfer/trade logging.

## Task 4c — NegRisk convert

Add `CTFConvert` opp from `scans/negrisk.py` (when a buy-all-NO set can be converted profitably) → `_build_legs` `polymarket_ctf` `action="convert"` → `ctf_client.build_convert`. This is what makes **Plan 01 capital-efficient** — cross-reference both ways.

## Tests

- Unit (no chain): `tests/test_ctf_fees.py`; `tests/test_ctf_build.py` — `build_split`/`build_merge` produce the correct selector + ABI-encoded calldata for a known `(condition_id, partition=[1,2], amount)` (assert against a fixture hex). Mock the RPC.
- `tests/test_ctf_scan.py` — synthetic books trigger `CTFMint` (sum bids > 1) and `CTFMerge` (sum asks < 1); none when within the no-arb band.
- **Integration (gated, manual):** an Amoy-testnet script that splits then merges a tiny position and asserts the round-trip — run by the operator, not in CI.

## Verification

```bash
pip install -r requirements.txt           # web3 now present
pytest tests/test_ctf_fees.py tests/test_ctf_build.py tests/test_ctf_scan.py -v
CTF_ENABLED=true python scanner.py --mode ctf --dry-run      # 4a: detect + simulate only
# 4b: operator runs the Amoy round-trip script manually before any mainnet flag flip
pytest tests/ -q
```

## Done criteria

- **4a:** `web3` added; `CTFClient` builds + gas-estimates split/merge/redeem/convert calldata (no broadcast); `scans/ctf.py` detects mint-sell + merge in dry-run; one week of dry-run logs reviewed.
- **4b:** signed merge round-trip proven on Amoy, then a single tiny mainnet merge with `CTF_MERGE_ENABLED=true`, dispute gate (Plan 05) active.
- **4c:** mint-and-sell (hedged) and NegRisk convert live; Plan 01 switched to the capital-efficient convert path.
- All `CTF_*` flags default off; addresses verified against the live deployment; `POLYMARKET_PRIVATE_KEY` raw-signing path reviewed against `SECURITY.md`.
