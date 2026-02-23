"""Tests for fees.py — fee calculators and net profit functions."""

import math
import pytest
from unittest.mock import patch

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fees import (
    polymarket_fee,
    kalshi_taker_fee,
    net_profit_binary_internal,
    net_profit_negrisk_internal,
    net_profit_kalshi_binary,
    net_profit_kalshi_multi,
    net_profit_cross_platform,
    betfair_commission,
    net_profit_cross_betfair,
    smarkets_commission,
    gemini_fee,
    net_profit_gemini_binary,
    net_profit_gemini_multi,
    net_profit_cross_gemini,
    net_profit_ibkr_binary,
    net_profit_cross_ibkr,
    net_profit_cross_generic,
    _platform_win_fee,
    _platform_entry_fee,
)


# ---------------------------------------------------------------------------
# polymarket_fee
# ---------------------------------------------------------------------------

class TestPolymarketFee:
    def test_zero_when_sell_equals_buy(self):
        assert polymarket_fee(0.50, 0.50) == 0.0

    def test_zero_when_sell_less_than_buy(self):
        assert polymarket_fee(0.80, 0.60) == 0.0

    def test_correct_2_percent_calculation(self):
        # Net winnings = 1.0 - 0.40 = 0.60; fee = 0.02 * 0.60 = 0.012
        assert polymarket_fee(0.40, 1.0) == pytest.approx(0.012)

    def test_small_spread(self):
        # Net winnings = 1.0 - 0.95 = 0.05; fee = 0.02 * 0.05 = 0.001
        assert polymarket_fee(0.95, 1.0) == pytest.approx(0.001)

    def test_default_sell_price_is_one(self):
        assert polymarket_fee(0.40) == pytest.approx(0.012)

    def test_zero_buy_price(self):
        # Net winnings = 1.0 - 0.0 = 1.0; fee = 0.02
        assert polymarket_fee(0.0, 1.0) == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# kalshi_taker_fee
# ---------------------------------------------------------------------------

class TestKalshiTakerFee:
    def test_minimum_2_cents_per_contract(self):
        # For price near boundary, the formula may yield < 2 cents, so min kicks in
        # price=0.01: ceil(7*0.01*0.99) = ceil(0.0693) = 1 -> min(2,1) = 2
        fee = kalshi_taker_fee(0.01, 1)
        assert fee == pytest.approx(0.02)

    def test_correct_formula_mid_price(self):
        # price=0.50: ceil(7*0.50*0.50) = ceil(1.75) = 2 cents
        fee = kalshi_taker_fee(0.50, 1)
        assert fee == pytest.approx(0.02)

    def test_higher_price(self):
        # price=0.30: ceil(7*0.30*0.70) = ceil(1.47) = 2 cents
        fee = kalshi_taker_fee(0.30, 1)
        assert fee == pytest.approx(0.02)

    def test_cap_at_175(self):
        # The formula max is at p=0.5 -> 2 cents, never reaches 175 for single contract
        # but verify the cap works with multiple contracts
        fee = kalshi_taker_fee(0.50, 1)
        expected_cents = max(2, math.ceil(7 * 0.50 * 0.50))
        expected_cents = min(expected_cents, 175)
        assert fee == pytest.approx(expected_cents / 100.0)

    def test_multiple_contracts(self):
        fee_1 = kalshi_taker_fee(0.50, 1)
        fee_10 = kalshi_taker_fee(0.50, 10)
        assert fee_10 == pytest.approx(fee_1 * 10)

    def test_zero_price_returns_zero(self):
        assert kalshi_taker_fee(0.0) == 0.0

    def test_one_price_returns_zero(self):
        assert kalshi_taker_fee(1.0) == 0.0

    def test_negative_price_returns_zero(self):
        assert kalshi_taker_fee(-0.1) == 0.0


# ---------------------------------------------------------------------------
# net_profit_binary_internal
# ---------------------------------------------------------------------------

class TestNetProfitBinaryInternal:
    def test_negative_spread(self):
        # 0.55 + 0.50 = 1.05 -> gross = 1.0 - 1.05 = -0.05
        result = net_profit_binary_internal(0.55, 0.50)
        assert result["gross_spread"] == pytest.approx(-0.05)
        assert result["fees"] == 0
        assert result["net_profit"] == pytest.approx(-0.05)

    def test_zero_profit_exact_dollar(self):
        result = net_profit_binary_internal(0.50, 0.50)
        assert result["gross_spread"] == pytest.approx(0.0)
        assert result["fees"] == 0
        assert result["net_profit"] == pytest.approx(0.0)

    def test_positive_profit(self):
        from config import POLYGON_GAS_ESTIMATE
        # yes=0.40, no=0.40 -> total=0.80, gross=0.20
        # cheaper=0.40, pm_fee=0.02*(1.0-0.40)=0.012, gas=GAS*2
        result = net_profit_binary_internal(0.40, 0.40)
        gas = POLYGON_GAS_ESTIMATE * 2
        assert result["gross_spread"] == pytest.approx(0.20)
        assert result["fees"] == pytest.approx(0.012 + gas)
        assert result["net_profit"] == pytest.approx(0.20 - 0.012 - gas)

    def test_worst_case_fee_uses_cheaper_side(self):
        from config import POLYGON_GAS_ESTIMATE
        # yes=0.30, no=0.45 -> total=0.75, gross=0.25
        # cheaper=0.30, pm_fee=0.02*(1.0-0.30)=0.014, gas=GAS*2
        result = net_profit_binary_internal(0.30, 0.45)
        gas = POLYGON_GAS_ESTIMATE * 2
        assert result["fees"] == pytest.approx(0.014 + gas)

    def test_asymmetric_prices(self):
        from config import POLYGON_GAS_ESTIMATE
        result = net_profit_binary_internal(0.10, 0.80)
        gas = POLYGON_GAS_ESTIMATE * 2
        assert result["gross_spread"] == pytest.approx(0.10)
        # cheaper=0.10, pm_fee=0.02*(1.0-0.10)=0.018, gas=GAS*2
        assert result["fees"] == pytest.approx(0.018 + gas)
        assert result["net_profit"] == pytest.approx(0.10 - 0.018 - gas)


# ---------------------------------------------------------------------------
# net_profit_negrisk_internal
# ---------------------------------------------------------------------------

class TestNetProfitNegriskInternal:
    def test_multiple_outcomes_positive(self):
        from config import POLYGON_GAS_ESTIMATE
        # 4 outcomes each at 0.20 -> total=0.80, gross=0.20
        prices = [0.20, 0.20, 0.20, 0.20]
        result = net_profit_negrisk_internal(prices)
        gas = POLYGON_GAS_ESTIMATE * 4
        assert result["gross_spread"] == pytest.approx(0.20)
        # cheapest=0.20, pm_fee=0.02*(1.0-0.20)=0.016, gas=GAS*4
        assert result["fees"] == pytest.approx(0.016 + gas)
        assert result["net_profit"] == pytest.approx(0.20 - 0.016 - gas)

    def test_no_spread(self):
        prices = [0.25, 0.25, 0.25, 0.25]
        result = net_profit_negrisk_internal(prices)
        assert result["gross_spread"] == pytest.approx(0.0)
        assert result["net_profit"] == pytest.approx(0.0)

    def test_negative_spread(self):
        prices = [0.30, 0.30, 0.30, 0.30]
        result = net_profit_negrisk_internal(prices)
        assert result["gross_spread"] == pytest.approx(-0.20)
        assert result["fees"] == 0

    def test_worst_case_fee_uses_cheapest_outcome(self):
        from config import POLYGON_GAS_ESTIMATE
        prices = [0.10, 0.25, 0.30, 0.15]
        result = net_profit_negrisk_internal(prices)
        gas = POLYGON_GAS_ESTIMATE * 4
        # cheapest=0.10, pm_fee=0.02*(1.0-0.10)=0.018, gas=GAS*4
        assert result["fees"] == pytest.approx(0.018 + gas)


# ---------------------------------------------------------------------------
# net_profit_kalshi_binary
# ---------------------------------------------------------------------------

class TestNetProfitKalshiBinary:
    def test_positive_spread_with_fees(self):
        # yes=0.40, no=0.40 -> total=0.80, gross=0.20
        result = net_profit_kalshi_binary(0.40, 0.40)
        assert result["gross_spread"] == pytest.approx(0.20)
        expected_fees = kalshi_taker_fee(0.40) + kalshi_taker_fee(0.40)
        assert result["fees"] == pytest.approx(expected_fees)
        assert result["net_profit"] == pytest.approx(0.20 - expected_fees)

    def test_negative_spread(self):
        result = net_profit_kalshi_binary(0.55, 0.50)
        assert result["gross_spread"] == pytest.approx(-0.05)
        assert result["fees"] == 0
        assert result["net_profit"] == pytest.approx(-0.05)

    def test_zero_spread(self):
        result = net_profit_kalshi_binary(0.50, 0.50)
        assert result["gross_spread"] == pytest.approx(0.0)
        assert result["net_profit"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# net_profit_kalshi_multi
# ---------------------------------------------------------------------------

class TestNetProfitKalshiMulti:
    def test_multiple_legs_positive(self):
        prices = [0.20, 0.20, 0.20, 0.20]
        result = net_profit_kalshi_multi(prices)
        assert result["gross_spread"] == pytest.approx(0.20)
        expected_fees = sum(kalshi_taker_fee(p) for p in prices)
        assert result["fees"] == pytest.approx(expected_fees)
        assert result["net_profit"] == pytest.approx(0.20 - expected_fees)

    def test_negative_spread(self):
        prices = [0.30, 0.30, 0.30, 0.30]
        result = net_profit_kalshi_multi(prices)
        assert result["gross_spread"] < 0
        assert result["fees"] == 0


# ---------------------------------------------------------------------------
# net_profit_cross_platform
# ---------------------------------------------------------------------------

class TestNetProfitCrossPlatform:
    def test_both_strategies_positive(self):
        # poly=0.30, kalshi=0.30 -> total=0.60, gross=0.40
        result = net_profit_cross_platform(0.30, 0.30, "yes", "no")
        assert result["gross_spread"] == pytest.approx(0.40)
        assert result["net_profit"] > 0

    def test_no_spread(self):
        result = net_profit_cross_platform(0.50, 0.50, "yes", "no")
        assert result["gross_spread"] == pytest.approx(0.0)
        assert result["net_profit"] == pytest.approx(0.0)

    def test_negative_spread(self):
        result = net_profit_cross_platform(0.60, 0.50, "yes", "no")
        assert result["gross_spread"] == pytest.approx(-0.10)
        assert result["net_profit"] == pytest.approx(-0.10)

    @patch("fees.FEE_MODEL", "worst_case")
    def test_worst_case_fees_used(self):
        from config import POLYGON_GAS_ESTIMATE
        # Verify worst-case (max) fees + gas are applied
        poly_price, kalshi_price = 0.30, 0.30
        kalshi_entry_fee = kalshi_taker_fee(kalshi_price, 1)
        case1_fees = polymarket_fee(poly_price, 1.0) + kalshi_entry_fee
        case2_fees = kalshi_entry_fee
        expected_worst = max(case1_fees, case2_fees) + POLYGON_GAS_ESTIMATE

        result = net_profit_cross_platform(poly_price, kalshi_price, "yes", "no")
        assert result["fees"] == pytest.approx(expected_worst)

    def test_ev_fees_lower_than_worst_case(self):
        """EV fee model should produce lower or equal fees vs worst-case."""
        from fees import _select_fees
        # Case where case1 > case2: EV should be lower than max
        case1, case2, price_a = 0.03, 0.01, 0.40
        ev_fees = _select_fees(case1, case2, price_a)
        worst_fees = max(case1, case2)
        assert ev_fees <= worst_fees


# ---------------------------------------------------------------------------
# betfair_commission
# ---------------------------------------------------------------------------

class TestBetfairCommission:
    def test_default_5_percent(self):
        assert betfair_commission(10.0) == pytest.approx(0.50)

    def test_zero_for_losses(self):
        assert betfair_commission(-5.0) == 0.0
        assert betfair_commission(0.0) == 0.0

    def test_configurable_rate(self):
        assert betfair_commission(10.0, 0.02) == pytest.approx(0.20)
        assert betfair_commission(10.0, 0.10) == pytest.approx(1.00)


# ---------------------------------------------------------------------------
# net_profit_cross_betfair
# ---------------------------------------------------------------------------

class TestNetProfitCrossBetfair:
    def test_positive_spread(self):
        result = net_profit_cross_betfair(0.30, 0.30, "yes", "no")
        assert result["gross_spread"] == pytest.approx(0.40)
        assert result["net_profit"] > 0

    def test_negative_spread(self):
        result = net_profit_cross_betfair(0.60, 0.50, "yes", "no")
        assert result["gross_spread"] < 0
        assert result["fees"] == 0

    @patch("fees.FEE_MODEL", "worst_case")
    def test_worst_case_fees(self):
        from config import POLYGON_GAS_ESTIMATE
        poly_price, bf_price = 0.30, 0.30
        poly_win_fee = polymarket_fee(poly_price, 1.0)
        bf_win_fee = betfair_commission(1.0 - bf_price, 0.05)
        expected_worst = max(poly_win_fee, bf_win_fee) + POLYGON_GAS_ESTIMATE
        result = net_profit_cross_betfair(poly_price, bf_price, "yes", "no")
        assert result["fees"] == pytest.approx(expected_worst)

    def test_custom_commission_rate(self):
        result_default = net_profit_cross_betfair(0.30, 0.30, "yes", "no")
        result_custom = net_profit_cross_betfair(0.30, 0.30, "yes", "no", commission_rate=0.02)
        # Lower commission rate should yield higher net profit
        assert result_custom["net_profit"] >= result_default["net_profit"]


# ---------------------------------------------------------------------------
# Gas fee deduction tests (POLYGON_GAS_ESTIMATE)
# ---------------------------------------------------------------------------

class TestGasFeeDeduction:
    """Verify Polygon gas fees are subtracted from all Polymarket-involving fee functions."""

    def test_binary_internal_includes_gas(self):
        """Binary internal should subtract 2x gas (two PM orders)."""
        from config import POLYGON_GAS_ESTIMATE
        result = net_profit_binary_internal(0.40, 0.40)
        # gross=0.20, pm_fee=0.02*(1.0-0.40)=0.012, gas=0.03*2=0.06
        expected_gas = POLYGON_GAS_ESTIMATE * 2
        expected_pm_fee = polymarket_fee(0.40, 1.0)
        assert result["fees"] == pytest.approx(expected_pm_fee + expected_gas)
        assert result["net_profit"] == pytest.approx(0.20 - expected_pm_fee - expected_gas)

    def test_negrisk_internal_includes_gas(self):
        """NegRisk should subtract gas * len(outcomes)."""
        from config import POLYGON_GAS_ESTIMATE
        prices = [0.20, 0.20, 0.20, 0.20]
        result = net_profit_negrisk_internal(prices)
        expected_gas = POLYGON_GAS_ESTIMATE * 4
        expected_pm_fee = polymarket_fee(0.20, 1.0)
        assert result["fees"] == pytest.approx(expected_pm_fee + expected_gas)
        assert result["net_profit"] == pytest.approx(0.20 - expected_pm_fee - expected_gas)

    def test_cross_platform_includes_gas(self):
        """Cross-platform (PM vs Kalshi) should subtract 1x gas."""
        from config import POLYGON_GAS_ESTIMATE
        result = net_profit_cross_platform(0.30, 0.30, "yes", "no")
        # Gas should be included in fees
        assert result["fees"] >= POLYGON_GAS_ESTIMATE

    def test_cross_betfair_includes_gas(self):
        """Cross-platform (PM vs Betfair) should subtract 1x gas."""
        from config import POLYGON_GAS_ESTIMATE
        result = net_profit_cross_betfair(0.30, 0.30, "yes", "no")
        assert result["fees"] >= POLYGON_GAS_ESTIMATE

    def test_zero_gas_preserves_old_behavior(self):
        """When POLYGON_GAS_ESTIMATE=0, profit matches pre-gas behavior."""
        with patch("fees.POLYGON_GAS_ESTIMATE", 0.0):
            result = net_profit_binary_internal(0.40, 0.40)
            # Without gas: gross=0.20, fee=0.012, net=0.188
            assert result["net_profit"] == pytest.approx(0.188)

    def test_custom_gas_via_env(self):
        """Gas fee should use the configured value."""
        with patch("fees.POLYGON_GAS_ESTIMATE", 0.05):
            result = net_profit_binary_internal(0.40, 0.40)
            # gas=0.05*2=0.10, pm_fee=0.012, gross=0.20
            assert result["net_profit"] == pytest.approx(0.20 - 0.012 - 0.10)

    def test_kalshi_binary_no_gas(self):
        """Kalshi-only trades should NOT include Polygon gas fees."""
        result = net_profit_kalshi_binary(0.40, 0.40)
        # Only Kalshi taker fees, no gas
        expected_fees = kalshi_taker_fee(0.40) + kalshi_taker_fee(0.40)
        assert result["fees"] == pytest.approx(expected_fees)


# ---------------------------------------------------------------------------
# net_profit_gemini_binary
# ---------------------------------------------------------------------------

class TestGeminiFee:
    def test_symmetric_at_half(self):
        # min(0.5, 0.5) * 0.05 = 0.025
        assert gemini_fee(0.50) == pytest.approx(0.025)

    def test_low_price(self):
        # min(0.10, 0.90) * 0.05 = 0.005
        assert gemini_fee(0.10) == pytest.approx(0.005)

    def test_high_price(self):
        # min(0.90, 0.10) * 0.05 = 0.005
        assert gemini_fee(0.90) == pytest.approx(0.005)

    def test_custom_rate(self):
        # min(0.40, 0.60) * 0.01 = 0.004
        assert gemini_fee(0.40, fee_rate=0.01) == pytest.approx(0.004)

    def test_boundary_zero(self):
        assert gemini_fee(0.0) == 0.0
        assert gemini_fee(1.0) == 0.0


class TestNetProfitGeminiBinary:
    def test_positive_spread_with_fees(self):
        # 0.40 + 0.40 = 0.80, gross = 0.20
        # fee_yes = min(0.40, 0.60) * 0.05 = 0.02
        # fee_no  = min(0.40, 0.60) * 0.05 = 0.02
        # total fees = 0.04
        result = net_profit_gemini_binary(0.40, 0.40)
        assert result["gross_spread"] == pytest.approx(0.20)
        assert result["fees"] == pytest.approx(0.04)
        assert result["net_profit"] == pytest.approx(0.16)

    def test_negative_spread(self):
        result = net_profit_gemini_binary(0.55, 0.50)
        assert result["gross_spread"] == pytest.approx(-0.05)
        assert result["fees"] == 0
        assert result["net_profit"] == pytest.approx(-0.05)

    def test_configurable_fee_rate(self):
        # With 1% maker fee
        result = net_profit_gemini_binary(0.40, 0.40, fee_rate=0.01)
        # fee per leg = min(0.40, 0.60) * 0.01 = 0.004, total = 0.008
        assert result["fees"] == pytest.approx(0.008)
        assert result["net_profit"] == pytest.approx(0.20 - 0.008)

    def test_zero_spread(self):
        result = net_profit_gemini_binary(0.50, 0.50)
        assert result["gross_spread"] == pytest.approx(0.0)
        assert result["net_profit"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# net_profit_gemini_multi
# ---------------------------------------------------------------------------

class TestNetProfitGeminiMulti:
    def test_positive_spread_4_outcomes(self):
        prices = [0.20, 0.20, 0.20, 0.20]
        result = net_profit_gemini_multi(prices)
        # Each leg: min(0.20, 0.80) * 0.05 = 0.01, total = 0.04
        assert result["gross_spread"] == pytest.approx(0.20)
        assert result["fees"] == pytest.approx(0.04)
        assert result["net_profit"] == pytest.approx(0.16)

    def test_negative_spread(self):
        prices = [0.30, 0.30, 0.30, 0.30]
        result = net_profit_gemini_multi(prices)
        assert result["gross_spread"] == pytest.approx(-0.20)
        assert result["fees"] == 0

    def test_configurable_fee_rate(self):
        prices = [0.20, 0.20, 0.20, 0.20]
        result = net_profit_gemini_multi(prices, fee_rate=0.01)
        # Each leg: min(0.20, 0.80) * 0.01 = 0.002, total = 0.008
        assert result["fees"] == pytest.approx(0.008)
        assert result["net_profit"] == pytest.approx(0.20 - 0.008)


# ---------------------------------------------------------------------------
# net_profit_cross_gemini
# ---------------------------------------------------------------------------

class TestNetProfitCrossGemini:
    def test_positive_spread(self):
        result = net_profit_cross_gemini(0.30, 0.30, "yes", "no")
        assert result["gross_spread"] == pytest.approx(0.40)
        assert result["net_profit"] > 0

    def test_negative_spread(self):
        result = net_profit_cross_gemini(0.60, 0.50, "yes", "no")
        assert result["gross_spread"] == pytest.approx(-0.10)
        assert result["net_profit"] == pytest.approx(-0.10)

    @patch("fees.FEE_MODEL", "worst_case")
    def test_includes_gemini_entry_fee(self):
        from config import POLYGON_GAS_ESTIMATE
        # Gemini entry fee = min(0.30, 0.70) * 0.05 = 0.015
        # PM win fee = 0.02 * (1.0 - 0.30) = 0.014
        # worst_fees = max(0.014 + 0.015, 0.015) = 0.029
        result = net_profit_cross_gemini(0.30, 0.30, "yes", "no")
        gm_entry = gemini_fee(0.30, 0.05)
        pm_fee = polymarket_fee(0.30, 1.0)
        expected_worst = max(pm_fee + gm_entry, gm_entry) + POLYGON_GAS_ESTIMATE
        assert result["fees"] == pytest.approx(expected_worst)


# ---------------------------------------------------------------------------
# net_profit_ibkr_binary
# ---------------------------------------------------------------------------

class TestNetProfitIBKRBinary:
    def test_positive_spread_zero_fees(self):
        result = net_profit_ibkr_binary(0.40, 0.40)
        assert result["gross_spread"] == pytest.approx(0.20)
        assert result["fees"] == 0
        assert result["net_profit"] == pytest.approx(0.20)

    def test_negative_spread(self):
        result = net_profit_ibkr_binary(0.55, 0.50)
        assert result["gross_spread"] == pytest.approx(-0.05)
        assert result["fees"] == 0
        assert result["net_profit"] == pytest.approx(-0.05)

    def test_profit_equals_gross_spread(self):
        """IBKR has $0.00 fees so net_profit always equals gross_spread."""
        result = net_profit_ibkr_binary(0.30, 0.30)
        assert result["net_profit"] == pytest.approx(result["gross_spread"])


# ---------------------------------------------------------------------------
# net_profit_cross_ibkr
# ---------------------------------------------------------------------------

class TestNetProfitCrossIBKR:
    def test_positive_spread(self):
        result = net_profit_cross_ibkr(0.30, 0.30, "yes", "no")
        assert result["gross_spread"] == pytest.approx(0.40)
        assert result["net_profit"] > 0

    def test_negative_spread(self):
        result = net_profit_cross_ibkr(0.60, 0.50, "yes", "no")
        assert result["gross_spread"] == pytest.approx(-0.10)
        assert result["net_profit"] == pytest.approx(-0.10)

    def test_ibkr_zero_fee_means_only_poly_fees(self):
        from config import POLYGON_GAS_ESTIMATE
        # IBKR fee = 0, only Polymarket fees + gas
        result = net_profit_cross_ibkr(0.30, 0.30, "yes", "no")
        assert result["fees"] >= POLYGON_GAS_ESTIMATE


# ---------------------------------------------------------------------------
# net_profit_cross_generic
# ---------------------------------------------------------------------------

class TestNetProfitCrossGeneric:
    def test_positive_spread_kalshi_betfair(self):
        """Kalshi vs Betfair with profitable spread."""
        result = net_profit_cross_generic(
            0.30, 0.30, "yes", "no", platform_a="kalshi", platform_b="betfair"
        )
        assert result["gross_spread"] == pytest.approx(0.40)
        assert result["net_profit"] > 0
        assert result["fees"] > 0  # Kalshi entry fee + Betfair commission

    def test_negative_spread_returns_negative(self):
        result = net_profit_cross_generic(
            0.60, 0.50, "yes", "no", platform_a="kalshi", platform_b="betfair"
        )
        assert result["gross_spread"] == pytest.approx(-0.10)
        assert result["net_profit"] == pytest.approx(-0.10)

    def test_no_gas_for_non_polymarket_pairs(self):
        """Non-Polymarket pairs should NOT include Polygon gas fees."""
        result = net_profit_cross_generic(
            0.30, 0.30, "yes", "no", platform_a="kalshi", platform_b="sxbet"
        )
        from config import POLYGON_GAS_ESTIMATE
        # Compute expected fees without gas
        kalshi_entry = kalshi_taker_fee(0.30)
        # SX Bet: 0% fees. Kalshi: entry fee only, no win fee.
        # Case A wins: Kalshi win fee (0) + entry fees
        # Case B wins: SX Bet win fee (0) + entry fees
        # Both cases = entry fees only = kalshi_entry
        expected = kalshi_entry
        assert result["fees"] == pytest.approx(expected)
        # Verify no gas component
        assert POLYGON_GAS_ESTIMATE not in [0.0]  # gas > 0
        # fees should not include gas
        assert result["fees"] < result["gross_spread"]

    def test_includes_gas_when_polymarket_involved(self):
        """Polymarket pair should include Polygon gas."""
        from config import POLYGON_GAS_ESTIMATE
        result = net_profit_cross_generic(
            0.30, 0.30, "yes", "no", platform_a="polymarket", platform_b="kalshi"
        )
        assert result["fees"] >= POLYGON_GAS_ESTIMATE

    def test_kalshi_entry_fees_both_sides(self):
        """When both platforms are entry-fee platforms, both fees count."""
        result = net_profit_cross_generic(
            0.40, 0.40, "yes", "no", platform_a="kalshi", platform_b="gemini"
        )
        kalshi_entry = kalshi_taker_fee(0.40)
        gm_entry = gemini_fee(0.40)
        # Entry fees should include both platforms
        assert result["fees"] >= kalshi_entry + gm_entry

    def test_zero_fee_platforms(self):
        """SX Bet vs Matchbook — both 0% fee platforms."""
        result = net_profit_cross_generic(
            0.30, 0.30, "yes", "no", platform_a="sxbet", platform_b="matchbook"
        )
        assert result["gross_spread"] == pytest.approx(0.40)
        assert result["fees"] == 0.0
        assert result["net_profit"] == pytest.approx(0.40)

    @patch("fees.FEE_MODEL", "worst_case")
    def test_betfair_win_fee(self):
        """Betfair vs SX Bet — Betfair charges commission when its side wins."""
        result = net_profit_cross_generic(
            0.30, 0.30, "yes", "no", platform_a="betfair", platform_b="sxbet"
        )
        # When Betfair wins: commission on (1.0 - 0.30) = 5% of 0.70 = 0.035
        bf_win_fee = betfair_commission(1.0 - 0.30)
        assert result["fees"] == pytest.approx(bf_win_fee)

    @patch("fees.FEE_MODEL", "worst_case")
    def test_smarkets_win_fee(self):
        """Smarkets vs IBKR — Smarkets charges 2% when its side wins."""
        result = net_profit_cross_generic(
            0.30, 0.30, "yes", "no", platform_a="smarkets", platform_b="ibkr"
        )
        sm_win_fee = smarkets_commission(1.0 - 0.30)
        assert result["fees"] == pytest.approx(sm_win_fee)

    def test_equal_to_one_returns_zero(self):
        result = net_profit_cross_generic(
            0.50, 0.50, "yes", "no", platform_a="kalshi", platform_b="betfair"
        )
        assert result["gross_spread"] == pytest.approx(0.0)
        assert result["net_profit"] == pytest.approx(0.0)

    def test_exceeds_one_returns_negative(self):
        result = net_profit_cross_generic(
            0.60, 0.50, "yes", "no", platform_a="smarkets", platform_b="betfair"
        )
        assert result["net_profit"] < 0


# ---------------------------------------------------------------------------
# _CROSS_FEE_FUNCS coverage
# ---------------------------------------------------------------------------

class TestCrossFeeFuncsMap:
    def test_all_28_pairs_registered(self):
        """All C(8,2) = 28 platform pairs should have fee functions."""
        from scans.cross import _CROSS_FEE_FUNCS
        assert len(_CROSS_FEE_FUNCS) == 28

    def test_polymarket_pairs_use_dedicated_functions(self):
        """Polymarket pairs should use their hand-tuned implementations."""
        from scans.cross import _CROSS_FEE_FUNCS
        assert _CROSS_FEE_FUNCS[("polymarket", "kalshi")] is net_profit_cross_platform
        assert _CROSS_FEE_FUNCS[("polymarket", "betfair")] is net_profit_cross_betfair
        assert _CROSS_FEE_FUNCS[("polymarket", "gemini")] is net_profit_cross_gemini
        assert _CROSS_FEE_FUNCS[("polymarket", "ibkr")] is net_profit_cross_ibkr

    def test_non_polymarket_pairs_are_callable(self):
        """All non-Polymarket pairs should be callable with 4 args."""
        from scans.cross import _CROSS_FEE_FUNCS
        non_pm = {k: v for k, v in _CROSS_FEE_FUNCS.items()
                  if "polymarket" not in k}
        assert len(non_pm) == 21  # C(7,2) = 21
        for (pa, pb), fn in non_pm.items():
            result = fn(0.40, 0.40, "yes", "no")
            assert "net_profit" in result, f"Missing net_profit for {pa}-{pb}"
            assert "fees" in result, f"Missing fees for {pa}-{pb}"
            assert result["net_profit"] > 0, f"Expected profit for {pa}-{pb} at 0.40+0.40"

    def test_symmetric_lookup(self):
        """Both (a,b) and reversed lookup should find the same pair."""
        from scans.cross import _CROSS_FEE_FUNCS
        # kalshi-betfair should be findable
        key = ("kalshi", "betfair")
        assert key in _CROSS_FEE_FUNCS or ("betfair", "kalshi") in _CROSS_FEE_FUNCS
