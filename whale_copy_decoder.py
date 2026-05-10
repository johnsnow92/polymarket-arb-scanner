"""Polymarket CTF Exchange calldata decoder.

Decodes raw transaction calldata for the Polymarket CTF Exchange contract
(0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e on Polygon mainnet) into a
structured dict that downstream scans can consume.

The on-chain Order struct exposed by the CTF Exchange is:

    struct Order {
        uint256 salt;
        address maker;
        address signer;
        address taker;
        uint256 tokenId;
        uint256 makerAmount;
        uint256 takerAmount;
        uint256 expiration;
        uint256 nonce;
        uint256 feeRateBps;
        uint8   side;             // 0 = BUY, 1 = SELL
        uint8   signatureType;
        bytes   signature;
    }

The decoder supports:
    fillOrder(Order, uint256)
    fillOrders(Order[], uint256[])
    matchOrders(Order, Order[], uint256, uint256[])
    cancelOrder(Order)
    cancelOrders(Order[])

Function selectors are computed at module load via ``eth_utils.keccak``
so the contract's canonical Solidity signatures are the single source of
truth. References for the struct + ABI:

- ``py_order_utils.model.order.Order`` (EIP-712 typed-data definition)
- Polymarket CTF Exchange on Polygonscan
  (https://polygonscan.com/address/0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e)

Used by scans/whale_copy.py to extract whale trade direction, token ID,
size, and price from raw on-chain calldata.
"""

from __future__ import annotations

import logging
from typing import Any

from eth_abi import decode as abi_decode
from eth_utils import keccak

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CTF Exchange constants
# ---------------------------------------------------------------------------

POLYMARKET_CTF_EXCHANGE = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"

# Solidity tuple type for the on-chain Order struct, in canonical ABI form.
ORDER_TUPLE = (
    "(uint256,address,address,address,uint256,uint256,"
    "uint256,uint256,uint256,uint256,uint8,uint8,bytes)"
)

# Order field names in the same positional order as ORDER_TUPLE. Used to
# zip the decoded tuple back into a labelled dict.
ORDER_FIELDS: tuple[str, ...] = (
    "salt", "maker", "signer", "taker", "tokenId",
    "makerAmount", "takerAmount", "expiration", "nonce",
    "feeRateBps", "side", "signatureType", "signature",
)

# Side enum (matches py_order_utils.model.sides).
SIDE_BUY = 0
SIDE_SELL = 1

# USDC has 6 decimals on Polygon. CTF tokens (ERC1155) have 6 decimals
# matching USDC. Polymarket's own SDK uses ``to_token_decimals`` with 1e6.
USDC_DECIMALS = 6
USDC_SCALE = 10 ** USDC_DECIMALS


def _selector(signature: str) -> str:
    """Return the 4-byte Solidity function selector as a lowercase hex string."""
    return keccak(text=signature)[:4].hex()


SELECTORS: dict[str, str] = {
    f"fillOrder({ORDER_TUPLE},uint256)": "fillOrder",
    f"fillOrders({ORDER_TUPLE}[],uint256[])": "fillOrders",
    f"matchOrders({ORDER_TUPLE},{ORDER_TUPLE}[],uint256,uint256[])": "matchOrders",
    f"cancelOrder({ORDER_TUPLE})": "cancelOrder",
    f"cancelOrders({ORDER_TUPLE}[])": "cancelOrders",
}

# Lookup: 4-byte selector hex (no 0x prefix) -> (canonical_signature, method_name)
_SELECTOR_LOOKUP: dict[str, tuple[str, str]] = {
    _selector(sig): (sig, name) for sig, name in SELECTORS.items()
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class CalldataDecodeError(ValueError):
    """Raised when calldata can't be decoded as a known CTF Exchange method."""


def normalize_calldata(raw: str | bytes | None) -> bytes | None:
    """Coerce calldata into bytes, accepting hex strings with or without 0x.

    Returns None for empty/None input rather than raising — empty calldata
    is a valid (albeit useless) ETH transfer.
    """
    if raw is None:
        return None
    if isinstance(raw, bytes):
        data = raw
    else:
        s = raw.strip()
        if not s:
            return None
        if s.startswith(("0x", "0X")):
            s = s[2:]
        if not s:
            return None
        try:
            data = bytes.fromhex(s)
        except ValueError as e:
            raise CalldataDecodeError(f"calldata is not valid hex: {e}") from e
    if len(data) < 4:
        return None
    return data


def identify_method(calldata: bytes) -> tuple[str, str] | None:
    """Look up (canonical_signature, method_name) by 4-byte selector.

    Returns None if the selector isn't a known CTF Exchange method.
    """
    selector_hex = calldata[:4].hex()
    return _SELECTOR_LOOKUP.get(selector_hex)


def _order_tuple_to_dict(order: tuple) -> dict[str, Any]:
    """Convert a decoded Order tuple into a labelled dict.

    Bytes-typed fields (signature) are returned as 0x-prefixed hex for
    JSON-friendliness; addresses are normalised to lowercase 0x form.
    """
    if len(order) != len(ORDER_FIELDS):
        raise CalldataDecodeError(
            f"Order tuple has {len(order)} fields, expected {len(ORDER_FIELDS)}"
        )
    out: dict[str, Any] = {}
    for name, value in zip(ORDER_FIELDS, order):
        if name in ("maker", "signer", "taker"):
            # eth_abi returns checksummed strings already; lowercase for stable comparison.
            out[name] = value.lower() if isinstance(value, str) else value
        elif name == "signature":
            out[name] = "0x" + value.hex() if isinstance(value, (bytes, bytearray)) else value
        else:
            out[name] = int(value)
    return out


def _decode_args(calldata: bytes, method_name: str) -> dict[str, Any]:
    """Run eth_abi.decode for the given method; return a labelled args dict."""
    args = calldata[4:]

    if method_name == "fillOrder":
        order, fill_amount = abi_decode([ORDER_TUPLE, "uint256"], args)
        return {
            "order": _order_tuple_to_dict(order),
            "fillAmount": int(fill_amount),
        }
    if method_name == "fillOrders":
        orders, fill_amounts = abi_decode(
            [f"{ORDER_TUPLE}[]", "uint256[]"], args,
        )
        return {
            "orders": [_order_tuple_to_dict(o) for o in orders],
            "fillAmounts": [int(x) for x in fill_amounts],
        }
    if method_name == "matchOrders":
        taker_order, maker_orders, taker_fill, maker_fills = abi_decode(
            [ORDER_TUPLE, f"{ORDER_TUPLE}[]", "uint256", "uint256[]"], args,
        )
        return {
            "takerOrder": _order_tuple_to_dict(taker_order),
            "makerOrders": [_order_tuple_to_dict(o) for o in maker_orders],
            "takerFillAmount": int(taker_fill),
            "makerFillAmounts": [int(x) for x in maker_fills],
        }
    if method_name == "cancelOrder":
        (order,) = abi_decode([ORDER_TUPLE], args)
        return {"order": _order_tuple_to_dict(order)}
    if method_name == "cancelOrders":
        (orders,) = abi_decode([f"{ORDER_TUPLE}[]"], args)
        return {"orders": [_order_tuple_to_dict(o) for o in orders]}

    # Should never happen — method_name comes from _SELECTOR_LOOKUP.
    raise CalldataDecodeError(f"unsupported method: {method_name}")


def decode_calldata(raw: str | bytes | None) -> dict[str, Any] | None:
    """Decode CTF Exchange calldata into a structured result.

    Returns ``None`` when:
    - calldata is empty / shorter than a 4-byte selector
    - the selector doesn't match any known CTF Exchange method

    Returns a dict otherwise:
        {
            "method": "<fillOrder|fillOrders|matchOrders|cancelOrder|cancelOrders>",
            "signature": "<canonical Solidity signature>",
            "selector": "<4-byte hex>",
            "args": <labelled-args dict per method>,
        }

    Raises ``CalldataDecodeError`` only when the input string is non-empty
    but malformed (e.g. invalid hex), so callers can distinguish "this is
    not a Polymarket CTF call" (return None) from "this calldata is
    structurally broken" (exception).
    """
    data = normalize_calldata(raw)
    if data is None:
        return None
    match = identify_method(data)
    if match is None:
        return None
    signature, method_name = match
    try:
        args = _decode_args(data, method_name)
    except Exception as e:
        raise CalldataDecodeError(
            f"failed to ABI-decode {method_name} args: {e}"
        ) from e
    return {
        "method": method_name,
        "signature": signature,
        "selector": "0x" + data[:4].hex(),
        "args": args,
    }


# ---------------------------------------------------------------------------
# Trade extraction
# ---------------------------------------------------------------------------


def _price_for_order(order: dict[str, Any]) -> float | None:
    """Compute the limit price (USDC per CTF token) for an Order.

    For BUY: maker spends USDC (makerAmount), receives tokens (takerAmount).
        price = makerAmount / takerAmount.
    For SELL: maker spends tokens (makerAmount), receives USDC (takerAmount).
        price = takerAmount / makerAmount.

    Returns None if amounts are zero or unknown side.
    """
    side = order.get("side")
    maker_amt = order.get("makerAmount", 0)
    taker_amt = order.get("takerAmount", 0)
    try:
        maker_amt = float(maker_amt)
        taker_amt = float(taker_amt)
    except (TypeError, ValueError):
        return None

    if maker_amt == 0 or taker_amt == 0:
        return None

    if side == SIDE_BUY:
        return maker_amt / taker_amt
    if side == SIDE_SELL:
        return taker_amt / maker_amt
    return None


def _token_amount_for_order(order: dict[str, Any]) -> float:
    """Return the size in token units (ERC1155 CTF tokens, scaled to humans)."""
    side = order.get("side")
    maker_amt = float(order.get("makerAmount", 0))
    taker_amt = float(order.get("takerAmount", 0))
    if side == SIDE_BUY:
        # tokens are on the taker side
        return taker_amt / USDC_SCALE
    if side == SIDE_SELL:
        return maker_amt / USDC_SCALE
    return 0.0


def extract_whale_trade(
    decoded: dict[str, Any],
    whale_address: str,
) -> dict[str, Any] | None:
    """Extract a whale-relevant trade summary from a decoded CTF call.

    The "whale" is the EOA that originated the on-chain transaction (from
    address). Returns a dict shaped for whale_copy opportunity dicts:

        {
            "whale_role": "taker" | "maker" | "operator",
            "whale_side": "BUY" | "SELL",
            "token_id": "<int as string>",
            "token_amount": float (in CTF token units),
            "price": float (USDC per token, 0..1),
            "maker_address": "0x..." (the counter-party, lowercase),
            "fill_amount_raw": int (raw makerAmount units filled),
        }

    Returns None for cancel methods, fillOrders/matchOrders (caller-side
    aggregation belongs in scans/whale_copy.py — those callers can
    iterate through ``decoded['args']['orders']`` themselves), or when
    required fields are missing.

    Whale-side inference rules for ``fillOrder``:
    - whale is taker → whale's effective side is the OPPOSITE of the
      maker order's side. (taker BUYs when maker SELLs, etc.)
    - whale is maker → whale's effective side is the maker order's side.
    """
    if not decoded:
        return None

    method = decoded.get("method")
    args = decoded.get("args", {})
    whale = whale_address.lower() if whale_address else ""

    if method != "fillOrder":
        # Multi-order calls and cancels are aggregated upstream.
        return None

    order = args.get("order") or {}
    fill_amount = args.get("fillAmount", 0)

    maker = (order.get("maker") or "").lower()
    side = order.get("side")
    token_id = order.get("tokenId")

    if side not in (SIDE_BUY, SIDE_SELL) or token_id is None:
        return None

    is_self_trade = maker == whale
    whale_role = "maker" if is_self_trade else "taker"
    if whale_role == "taker":
        whale_side = "SELL" if side == SIDE_BUY else "BUY"
    else:
        whale_side = "BUY" if side == SIDE_BUY else "SELL"

    price = _price_for_order(order)
    full_size = _token_amount_for_order(order)

    # fillAmount is denominated in the maker's makerAmount units (USDC for
    # BUY, tokens for SELL). Compute the actual filled token amount.
    fill_fraction = 0.0
    maker_amt = float(order.get("makerAmount", 0))
    if maker_amt > 0:
        fill_fraction = min(1.0, float(fill_amount) / maker_amt)
    filled_tokens = full_size * fill_fraction if full_size > 0 else 0.0

    return {
        "whale_role": whale_role,
        "whale_side": whale_side,
        "token_id": str(token_id),
        "token_amount": filled_tokens,
        "price": price,
        "maker_address": maker,
        "fill_amount_raw": int(fill_amount),
    }
