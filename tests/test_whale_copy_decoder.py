"""Tests for whale_copy_decoder.py — Polymarket CTF Exchange calldata decoder.

Fixtures are constructed via ``eth_abi.encode`` so the round-trip
decode is deterministic and independent of network access. Five real
Polymarket method signatures are exercised: fillOrder, fillOrders,
matchOrders, cancelOrder, cancelOrders.

Stable module reference (``import whale_copy_decoder as wcd``) follows
the pattern documented in tests/test_time_decay_refiner.py — see that
file for the full rationale on cross-test sys.modules pollution.
"""

import os
import sys

import pytest
from eth_abi import encode as abi_encode
from eth_utils import keccak

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import whale_copy_decoder as wcd


# ---------------------------------------------------------------------------
# Test helpers — build canonical CTF calldata fixtures
# ---------------------------------------------------------------------------


_ORDER_TUPLE = wcd.ORDER_TUPLE  # canonical Solidity tuple type


def _selector_for(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def _make_order(
    *,
    salt: int = 1,
    maker: str = "0x" + "aa" * 20,
    signer: str | None = None,
    taker: str = "0x" + "00" * 20,
    token_id: int = 0x123456789abcdef,
    maker_amount: int = 100_000_000,   # 100 USDC (6 decimals)
    taker_amount: int = 200_000_000,   # 200 tokens
    expiration: int = 0,
    nonce: int = 0,
    fee_rate_bps: int = 100,
    side: int = wcd.SIDE_BUY,
    signature_type: int = 0,
    signature: bytes = b"",
) -> tuple:
    """Return an Order tuple in the order eth_abi expects."""
    return (
        salt,
        maker,
        signer if signer is not None else maker,
        taker,
        token_id,
        maker_amount,
        taker_amount,
        expiration,
        nonce,
        fee_rate_bps,
        side,
        signature_type,
        signature,
    )


def _encode_call(signature: str, types: list[str], values: list) -> bytes:
    return _selector_for(signature) + abi_encode(types, values)


# ---------------------------------------------------------------------------
# normalize_calldata + identify_method
# ---------------------------------------------------------------------------


class TestNormalizeCalldata:
    def test_accepts_0x_prefix(self):
        data = wcd.normalize_calldata("0x" + "ab" * 4)
        assert data == bytes.fromhex("ab" * 4)

    def test_accepts_no_prefix(self):
        data = wcd.normalize_calldata("ab" * 4)
        assert data == bytes.fromhex("ab" * 4)

    def test_accepts_bytes(self):
        data = wcd.normalize_calldata(b"\x12\x34\x56\x78")
        assert data == b"\x12\x34\x56\x78"

    def test_returns_none_on_empty(self):
        assert wcd.normalize_calldata("") is None
        assert wcd.normalize_calldata("0x") is None
        assert wcd.normalize_calldata(None) is None

    def test_returns_none_on_too_short(self):
        # Less than a 4-byte selector cannot decode.
        assert wcd.normalize_calldata("0x1234") is None

    def test_raises_on_bad_hex(self):
        with pytest.raises(wcd.CalldataDecodeError):
            wcd.normalize_calldata("0xZZZZZZZZ")


class TestIdentifyMethod:
    def test_fill_order_selector(self):
        sig = f"fillOrder({_ORDER_TUPLE},uint256)"
        data = _selector_for(sig) + b"\x00" * 32
        match = wcd.identify_method(data)
        assert match == (sig, "fillOrder")

    def test_unknown_selector_returns_none(self):
        data = b"\xde\xad\xbe\xef" + b"\x00" * 32
        assert wcd.identify_method(data) is None


# ---------------------------------------------------------------------------
# decode_calldata — round-trip per method
# ---------------------------------------------------------------------------


class TestDecodeFillOrder:
    def test_decodes_fillorder_buy(self):
        order = _make_order(side=wcd.SIDE_BUY, token_id=0xABCD)
        sig = f"fillOrder({_ORDER_TUPLE},uint256)"
        data = _encode_call(sig, [_ORDER_TUPLE, "uint256"], [order, 50_000_000])

        result = wcd.decode_calldata(data)

        assert result is not None
        assert result["method"] == "fillOrder"
        assert result["signature"] == sig
        order_dict = result["args"]["order"]
        assert order_dict["side"] == wcd.SIDE_BUY
        assert order_dict["tokenId"] == 0xABCD
        assert order_dict["maker"] == "0x" + "aa" * 20
        assert result["args"]["fillAmount"] == 50_000_000

    def test_decodes_fillorder_with_signature_bytes(self):
        order = _make_order(signature=b"\xde\xad\xbe\xef")
        sig = f"fillOrder({_ORDER_TUPLE},uint256)"
        data = _encode_call(sig, [_ORDER_TUPLE, "uint256"], [order, 1])

        result = wcd.decode_calldata(data)
        assert result is not None
        assert result["args"]["order"]["signature"] == "0xdeadbeef"

    def test_decodes_fillorder_from_hex_string(self):
        order = _make_order()
        sig = f"fillOrder({_ORDER_TUPLE},uint256)"
        data = _encode_call(sig, [_ORDER_TUPLE, "uint256"], [order, 1])
        # Pass as a 0x-prefixed hex string (Polygonscan format).
        result = wcd.decode_calldata("0x" + data.hex())
        assert result is not None
        assert result["method"] == "fillOrder"


class TestDecodeFillOrders:
    def test_decodes_fillorders_batch(self):
        o1 = _make_order(token_id=1, side=wcd.SIDE_BUY)
        o2 = _make_order(token_id=2, side=wcd.SIDE_SELL)
        sig = f"fillOrders({_ORDER_TUPLE}[],uint256[])"
        data = _encode_call(
            sig, [f"{_ORDER_TUPLE}[]", "uint256[]"], [[o1, o2], [10, 20]],
        )

        result = wcd.decode_calldata(data)

        assert result is not None
        assert result["method"] == "fillOrders"
        orders = result["args"]["orders"]
        fills = result["args"]["fillAmounts"]
        assert len(orders) == 2
        assert orders[0]["tokenId"] == 1
        assert orders[1]["tokenId"] == 2
        assert fills == [10, 20]


class TestDecodeMatchOrders:
    def test_decodes_matchorders(self):
        taker = _make_order(token_id=99, side=wcd.SIDE_BUY)
        maker_a = _make_order(token_id=99, side=wcd.SIDE_SELL)
        maker_b = _make_order(token_id=99, side=wcd.SIDE_SELL, maker="0x" + "bb" * 20)
        sig = f"matchOrders({_ORDER_TUPLE},{_ORDER_TUPLE}[],uint256,uint256[])"
        data = _encode_call(
            sig,
            [_ORDER_TUPLE, f"{_ORDER_TUPLE}[]", "uint256", "uint256[]"],
            [taker, [maker_a, maker_b], 7, [3, 4]],
        )

        result = wcd.decode_calldata(data)

        assert result is not None
        assert result["method"] == "matchOrders"
        assert result["args"]["takerOrder"]["tokenId"] == 99
        assert len(result["args"]["makerOrders"]) == 2
        assert result["args"]["takerFillAmount"] == 7
        assert result["args"]["makerFillAmounts"] == [3, 4]


class TestDecodeCancels:
    def test_decodes_cancel_order(self):
        order = _make_order()
        sig = f"cancelOrder({_ORDER_TUPLE})"
        data = _encode_call(sig, [_ORDER_TUPLE], [order])

        result = wcd.decode_calldata(data)
        assert result is not None
        assert result["method"] == "cancelOrder"
        assert result["args"]["order"]["maker"] == "0x" + "aa" * 20

    def test_decodes_cancel_orders_batch(self):
        orders = [_make_order(salt=i) for i in range(3)]
        sig = f"cancelOrders({_ORDER_TUPLE}[])"
        data = _encode_call(sig, [f"{_ORDER_TUPLE}[]"], [orders])

        result = wcd.decode_calldata(data)
        assert result is not None
        assert result["method"] == "cancelOrders"
        assert len(result["args"]["orders"]) == 3


class TestUnknownAndMalformed:
    def test_unknown_selector_returns_none(self):
        data = b"\x00\x00\x00\x00" + b"\x11" * 32
        assert wcd.decode_calldata(data) is None

    def test_empty_calldata_returns_none(self):
        assert wcd.decode_calldata("0x") is None
        assert wcd.decode_calldata(None) is None

    def test_invalid_hex_raises(self):
        with pytest.raises(wcd.CalldataDecodeError):
            wcd.decode_calldata("0xZZ")

    def test_truncated_args_raises(self):
        sig = f"fillOrder({_ORDER_TUPLE},uint256)"
        # Selector but no args — eth_abi will refuse to decode.
        data = _selector_for(sig) + b"\x00" * 8
        with pytest.raises(wcd.CalldataDecodeError):
            wcd.decode_calldata(data)


# ---------------------------------------------------------------------------
# extract_whale_trade — semantic extraction
# ---------------------------------------------------------------------------


class TestExtractWhaleTrade:
    def _decode_fillorder(self, **order_kwargs):
        # Allow callers to override fillAmount; otherwise default to a
        # full fill (== makerAmount).
        fill_amount = order_kwargs.pop(
            "_fill_amount", order_kwargs.get("maker_amount", 100_000_000),
        )
        order = _make_order(**order_kwargs)
        sig = f"fillOrder({_ORDER_TUPLE},uint256)"
        data = _encode_call(sig, [_ORDER_TUPLE, "uint256"], [order, fill_amount])
        return wcd.decode_calldata(data)

    def test_taker_buy_when_maker_sells(self):
        # Maker is selling tokens for USDC. Whale (taker) is BUYing tokens.
        decoded = self._decode_fillorder(
            maker="0x" + "aa" * 20,
            side=wcd.SIDE_SELL,
            maker_amount=200_000_000,   # 200 tokens
            taker_amount=100_000_000,   # 100 USDC
            token_id=0xABCD,
        )
        whale_addr = "0x" + "ee" * 20  # not the maker
        trade = wcd.extract_whale_trade(decoded, whale_addr)
        assert trade is not None
        assert trade["whale_role"] == "taker"
        assert trade["whale_side"] == "BUY"
        assert trade["token_id"] == str(0xABCD)
        # price = takerAmount / makerAmount = 100/200 = 0.5
        assert trade["price"] == pytest.approx(0.5)
        # token_amount in token units, fully filled = 200 / 1e6
        assert trade["token_amount"] == pytest.approx(200.0)

    def test_taker_sell_when_maker_buys(self):
        # Maker is buying tokens with USDC. Whale (taker) is SELLing tokens.
        decoded = self._decode_fillorder(
            maker="0x" + "aa" * 20,
            side=wcd.SIDE_BUY,
            maker_amount=100_000_000,   # 100 USDC
            taker_amount=200_000_000,   # 200 tokens
        )
        whale_addr = "0x" + "ee" * 20
        trade = wcd.extract_whale_trade(decoded, whale_addr)
        assert trade is not None
        assert trade["whale_role"] == "taker"
        assert trade["whale_side"] == "SELL"
        # price = makerAmount / takerAmount = 100/200 = 0.5
        assert trade["price"] == pytest.approx(0.5)

    def test_partial_fill_scales_token_amount(self):
        # Maker SELL: full size = 200 tokens. Fill 50% of makerAmount.
        decoded = self._decode_fillorder(
            side=wcd.SIDE_SELL,
            maker_amount=200_000_000,
            taker_amount=100_000_000,
            _fill_amount=100_000_000,  # 50% of makerAmount
        )
        whale_addr = "0x" + "ee" * 20
        trade = wcd.extract_whale_trade(decoded, whale_addr)
        assert trade is not None
        assert trade["token_amount"] == pytest.approx(100.0)

    def test_self_trade_is_maker_role(self):
        whale_addr = "0x" + "aa" * 20  # same as maker
        decoded = self._decode_fillorder(maker=whale_addr, side=wcd.SIDE_BUY)
        trade = wcd.extract_whale_trade(decoded, whale_addr)
        assert trade is not None
        assert trade["whale_role"] == "maker"
        # Maker BUY → whale is BUYing
        assert trade["whale_side"] == "BUY"

    def test_returns_none_for_non_fill_methods(self):
        # cancelOrder is not a copy-tradeable signal.
        order = _make_order()
        sig = f"cancelOrder({_ORDER_TUPLE})"
        data = _encode_call(sig, [_ORDER_TUPLE], [order])
        decoded = wcd.decode_calldata(data)
        assert wcd.extract_whale_trade(decoded, "0x" + "ee" * 20) is None

    def test_returns_none_for_fillorders_aggregate(self):
        # Multi-order calls are aggregated upstream; extractor returns None.
        o = _make_order()
        sig = f"fillOrders({_ORDER_TUPLE}[],uint256[])"
        data = _encode_call(sig, [f"{_ORDER_TUPLE}[]", "uint256[]"], [[o], [1]])
        decoded = wcd.decode_calldata(data)
        assert wcd.extract_whale_trade(decoded, "0x" + "ee" * 20) is None

    def test_returns_none_when_decoded_is_none(self):
        assert wcd.extract_whale_trade(None, "0x" + "ee" * 20) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Sanity check — selectors are stable (regression guard)
# ---------------------------------------------------------------------------


class TestSelectorStability:
    """Locked-in selectors so renaming a signature would fail loudly."""

    def test_fillorder_selector(self):
        sig = f"fillOrder({_ORDER_TUPLE},uint256)"
        assert _selector_for(sig).hex() == "fe729aaf"

    def test_fillorders_selector(self):
        sig = f"fillOrders({_ORDER_TUPLE}[],uint256[])"
        assert _selector_for(sig).hex() == "d798eff6"

    def test_matchorders_selector(self):
        sig = f"matchOrders({_ORDER_TUPLE},{_ORDER_TUPLE}[],uint256,uint256[])"
        assert _selector_for(sig).hex() == "e60f0c05"

    def test_cancelorder_selector(self):
        sig = f"cancelOrder({_ORDER_TUPLE})"
        assert _selector_for(sig).hex() == "a6dfcf86"

    def test_cancelorders_selector(self):
        sig = f"cancelOrders({_ORDER_TUPLE}[])"
        assert _selector_for(sig).hex() == "fa950b48"
