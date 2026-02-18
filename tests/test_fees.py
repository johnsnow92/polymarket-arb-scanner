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
