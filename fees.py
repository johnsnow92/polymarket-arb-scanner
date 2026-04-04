"""Fee calculators for Polymarket and Kalshi."""

import logging
import math

from config import (
    FEE_MODEL,
    KALSHI_FEE_CAP_CENTS,
    POLYGON_GAS_ESTIMATE,
    POLYMARKET_DEFAULT_TAKER_RATE,
    GEMINI_TAKER_RATE,
    GEMINI_MAKER_RATE,
    KALSHI_MAKER_MULTIPLIER,
)

logger = logging.getLogger(__name__)


def _select_fees(case_a_fees: float, case_b_fees: float, price_a: float) -> float:
    """Select fee estimate based on the configured FEE_MODEL.

    Args:
        case_a_fees: Fees when side A wins.
        case_b_fees: Fees when side B wins.
        price_a: Buy price on side A, used as probability proxy for EV model.

    Returns:
        Fee estimate (worst-case or expected-value depending on FEE_MODEL).
    """
    if FEE_MODEL == "expected_value":
        # Use price as proxy for probability of that side winning
        prob_a = max(0.0, min(1.0, price_a))
        return prob_a * case_b_fees + (1.0 - prob_a) * case_a_fees
    return max(case_a_fees, case_b_fees)


def polymarket_taker_fee(price: float, contracts: int = 1,
                         fee_rate: float | None = None) -> float:
    """Polymarket dynamic taker fee (March 2026 model).

    Formula: fee_rate * C * P * (1 - P).
    Makers pay 0%. Default rate 0.04 (politics/tech), overridable per market.
    Fee is charged at trade entry, not at settlement.

    Args:
        price: Trade price in [0, 1].
        contracts: Number of contracts (default 1).
        fee_rate: Override fee rate; uses POLYMARKET_DEFAULT_TAKER_RATE if None.

    Returns:
        Total fee in dollars.
    """
    if price <= 0 or price >= 1:
        return 0.0
    rate = fee_rate if fee_rate is not None else POLYMARKET_DEFAULT_TAKER_RATE
    return rate * contracts * price * (1.0 - price)


def polymarket_fee(buy_price: float, sell_price: float = 1.0) -> float:
    """DEPRECATED: Use polymarket_taker_fee() instead.

    Legacy settlement-fee model (2% on net winnings). Kept as alias for
    backward compatibility with cross-platform helpers that still use it.
    New code should call polymarket_taker_fee() directly.
    """
    if sell_price <= buy_price:
        return 0.0
    net_winnings = sell_price - buy_price
    return 0.02 * net_winnings


def kalshi_taker_fee(price: float, contracts: int = 1) -> float:
    """Calculate Kalshi taker fee.

    Formula: ceil(0.07 * C * P * (1 - P)) per contract, in cents.
    Minimum: $0.02 per contract (2 cents).
    Maximum: 1.75 cents per contract (some tiers).

    Returns total fee in dollars.
    """
    if price <= 0 or price >= 1:
        return 0.0
    # Fee per contract in cents
    fee_cents = max(2, math.ceil(7 * price * (1 - price)))
    # Cap per contract in cents (default 175 = $1.75, effectively no cap for retail)
    fee_cents = min(fee_cents, KALSHI_FEE_CAP_CENTS)
    return (fee_cents * contracts) / 100.0


def kalshi_maker_fee(price: float, contracts: int = 1) -> float:
    """Kalshi maker fee — lower than taker. Returns total in dollars.

    Formula: ceil(KALSHI_MAKER_MULTIPLIER * P * (1 - P)) per contract in cents.
    Minimum 1 cent per contract. Capped at KALSHI_FEE_CAP_CENTS per contract.

    Args:
        price: Trade price in [0, 1].
        contracts: Number of contracts (default 1).

    Returns:
        Total fee in dollars.
    """
    if price <= 0 or price >= 1:
        return 0.0
    fee_cents = max(1, math.ceil(KALSHI_MAKER_MULTIPLIER * price * (1 - price)))
    fee_cents = min(fee_cents, KALSHI_FEE_CAP_CENTS)
    return (fee_cents * contracts) / 100.0


def net_profit_binary_internal(yes_price: float, no_price: float) -> dict:
    """Calculate net profit for a Polymarket binary arbitrage.

    Buy YES + NO. One always pays $1.00.
    Profit = $1.00 - (yes_price + no_price) - fees.

    March 2026 model: fee is charged at entry (rate * P * (1-P)), not on winnings.
    Both legs pay the taker entry fee.
    """
    total_cost = yes_price + no_price
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    # Entry fees: both YES and NO legs pay taker fee at trade time
    fee = polymarket_taker_fee(yes_price) + polymarket_taker_fee(no_price)

    # Gas cost: two Polygon transactions (buy YES + buy NO)
    gas = POLYGON_GAS_ESTIMATE * 2

    return {
        "gross_spread": gross_spread,
        "fees": fee + gas,
        "net_profit": gross_spread - fee - gas,
    }


def net_profit_negrisk_internal(yes_prices: list[float]) -> dict:
    """Calculate net profit for a NegRisk (multi-outcome) arbitrage.

    Buy one YES share of every outcome. Exactly one pays $1.00.
    Profit = $1.00 - sum(prices) - fees.

    March 2026 model: all legs pay taker entry fee at trade time.
    """
    total_cost = sum(yes_prices)
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    # Entry fees: each outcome leg pays taker fee at trade time
    fee = sum(polymarket_taker_fee(p) for p in yes_prices)

    # Gas cost: one Polygon transaction per outcome
    gas = POLYGON_GAS_ESTIMATE * len(yes_prices)

    return {
        "gross_spread": gross_spread,
        "fees": fee + gas,
        "net_profit": gross_spread - fee - gas,
    }


def net_profit_kalshi_binary(yes_price: float, no_price: float) -> dict:
    """Calculate net profit for a Kalshi binary arbitrage.

    Buy YES + NO on the same market. One always pays $1.00.
    Both legs pay Kalshi taker fee at entry.
    """
    total_cost = yes_price + no_price
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    fees = kalshi_taker_fee(yes_price) + kalshi_taker_fee(no_price)
    return {
        "gross_spread": gross_spread,
        "fees": fees,
        "net_profit": gross_spread - fees,
    }


def net_profit_kalshi_multi(yes_prices: list[float]) -> dict:
    """Calculate net profit for a Kalshi multi-outcome arbitrage.

    Buy YES on each outcome in an event. Exactly one pays $1.00.
    Each leg pays Kalshi taker fee at entry.
    """
    total_cost = sum(yes_prices)
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    fees = sum(kalshi_taker_fee(p) for p in yes_prices)
    return {
        "gross_spread": gross_spread,
        "fees": fees,
        "net_profit": gross_spread - fees,
    }


def net_profit_cross_platform(
    poly_price: float,
    kalshi_price: float,
    poly_side: str,
    kalshi_side: str,
) -> dict:
    """Calculate net profit for cross-platform arbitrage.

    poly_side/kalshi_side: 'yes' or 'no' -- what we're buying on each platform.
    One of the two positions will win $1.00, the other $0.00.
    """
    poly_cost = poly_price
    kalshi_cost = kalshi_price
    total_cost = poly_cost + kalshi_cost

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    # Both legs pay entry fees at trade time (March 2026 model)
    pm_entry_fee = polymarket_taker_fee(poly_price)
    kalshi_entry_fee = kalshi_taker_fee(kalshi_price, 1)

    # Both fees are charged at entry regardless of outcome — no case distinction needed
    fees = pm_entry_fee + kalshi_entry_fee
    gas = POLYGON_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": fees + gas,
        "net_profit": gross_spread - fees - gas,
    }


# ---------------------------------------------------------------------------
# Betfair fee calculations
# ---------------------------------------------------------------------------

def betfair_commission(net_winnings: float, commission_rate: float = 0.05) -> float:
    """Betfair charges 2-5% commission on net winnings per market.

    The rate depends on the user's discount rate (based on activity).
    Default 5% is the standard rate for new/low-volume users.
    """
    if net_winnings <= 0:
        return 0.0
    return net_winnings * commission_rate


def net_profit_cross_betfair(
    poly_price: float,
    bf_price: float,
    poly_side: str,
    bf_side: str,
    commission_rate: float = 0.05,
) -> dict:
    """Calculate net profit for cross-platform arbitrage: Polymarket vs Betfair.

    poly_side/bf_side: 'yes' or 'no' -- what we're buying on each platform.
    One of the two positions will win $1.00, the other $0.00.
    """
    total_cost = poly_price + bf_price

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    # Polymarket leg pays entry fee at trade time (March 2026 model)
    pm_entry_fee = polymarket_taker_fee(poly_price)

    # Case 1 (Poly wins): PM entry fee + no Betfair commission (BF side lost)
    case1_fees = pm_entry_fee

    # Case 2 (Betfair wins): PM entry fee + Betfair commission on net winnings
    bf_win_profit = 1.0 - bf_price
    case2_fees = pm_entry_fee + betfair_commission(bf_win_profit, commission_rate)

    # Fee estimate + Polygon gas for the PM leg
    fees = _select_fees(case1_fees, case2_fees, poly_price)
    gas = POLYGON_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": fees + gas,
        "net_profit": gross_spread - fees - gas,
    }


# ---------------------------------------------------------------------------
# Spread (intra-platform) fee calculations
# ---------------------------------------------------------------------------

def net_profit_spread_polymarket(ask: float, bid: float) -> dict:
    """Calculate net profit for a Polymarket spread capture (buy at ask, sell at bid).

    Round-trip on same token — no CLOB fee, only Polygon gas for 2 txns.
    """
    if bid <= ask:
        return {"gross_spread": bid - ask, "fees": 0, "net_profit": bid - ask}

    gross = bid - ask
    gas = POLYGON_GAS_ESTIMATE * 2
    return {
        "gross_spread": gross,
        "fees": gas,
        "net_profit": gross - gas,
    }


# ---------------------------------------------------------------------------
# Betfair standalone fee calculations
# ---------------------------------------------------------------------------

def net_profit_betfair_backall(implied_probs: list[float], commission_rate: float = 0.05) -> dict:
    """Calculate net profit for a Betfair back-all arbitrage.

    Back all runners. Sum of implied probabilities < 1.0 means under-round book.
    Exactly one runner wins; commission on net winnings.
    """
    total_cost = sum(implied_probs)
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    # Winner pays out $1. Commission on net winnings (1 - cost of winning bet).
    cheapest = min(implied_probs)
    net_winnings = 1.0 - cheapest
    fee = betfair_commission(net_winnings, commission_rate)

    return {
        "gross_spread": gross_spread,
        "fees": fee,
        "net_profit": gross_spread - fee,
    }


def net_profit_betfair_backlay(back_price: float, lay_price: float, commission_rate: float = 0.05) -> dict:
    """Calculate net profit for a Betfair back-lay arbitrage on same runner.

    Back at back_price, lay at lay_price. Profit when back < lay (crossed book).
    back_price and lay_price are in implied probability (0-1) terms.
    """
    if lay_price <= back_price:
        return {"gross_spread": 0, "fees": 0, "net_profit": 0}

    # Gross profit from the spread
    gross = lay_price - back_price

    # Commission applies to net market profit
    fee = betfair_commission(gross, commission_rate)

    return {
        "gross_spread": gross,
        "fees": fee,
        "net_profit": gross - fee,
    }


# ---------------------------------------------------------------------------
# Smarkets fee calculations
# ---------------------------------------------------------------------------

def smarkets_commission(net_winnings: float, commission_rate: float = 0.02) -> float:
    """Smarkets charges 2% commission on net winnings.

    The rate is fixed at 2% for most users (lower than Betfair's 5% default).
    """
    if net_winnings <= 0:
        return 0.0
    return net_winnings * commission_rate


def net_profit_smarkets_backall(implied_probs: list[float], commission_rate: float = 0.02) -> dict:
    """Calculate net profit for a Smarkets back-all arbitrage.

    Back all runners. Sum of implied probabilities < 1.0 means under-round book.
    Exactly one runner wins; commission on net winnings.
    """
    total_cost = sum(implied_probs)
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    # Winner pays out $1. Commission on net winnings (1 - cost of winning bet).
    cheapest = min(implied_probs)
    net_winnings = 1.0 - cheapest
    fee = smarkets_commission(net_winnings, commission_rate)

    return {
        "gross_spread": gross_spread,
        "fees": fee,
        "net_profit": gross_spread - fee,
    }


def net_profit_smarkets_backlay(back_price: float, lay_price: float, commission_rate: float = 0.02) -> dict:
    """Calculate net profit for a Smarkets back-lay arbitrage on same runner.

    Back at back_price, lay at lay_price. Profit when back < lay (crossed book).
    back_price and lay_price are in implied probability (0-1) terms.
    """
    if lay_price <= back_price:
        return {"gross_spread": 0, "fees": 0, "net_profit": 0}

    gross = lay_price - back_price
    fee = smarkets_commission(gross, commission_rate)

    return {
        "gross_spread": gross,
        "fees": fee,
        "net_profit": gross - fee,
    }


def net_profit_cross_smarkets(
    poly_price: float,
    sm_price: float,
    poly_side: str,
    sm_side: str,
    commission_rate: float = 0.02,
) -> dict:
    """Calculate net profit for cross-platform arbitrage: Polymarket vs Smarkets.

    poly_side/sm_side: 'yes' or 'no' -- what we're buying on each platform.
    One of the two positions will win $1.00, the other $0.00.
    """
    total_cost = poly_price + sm_price

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    # Polymarket leg pays entry fee at trade time (March 2026 model)
    pm_entry_fee = polymarket_taker_fee(poly_price)

    # Case 1 (Poly wins): PM entry fee + no Smarkets commission (SM side lost)
    case1_fees = pm_entry_fee

    # Case 2 (Smarkets wins): PM entry fee + Smarkets commission on net winnings
    sm_win_profit = 1.0 - sm_price
    case2_fees = pm_entry_fee + smarkets_commission(sm_win_profit, commission_rate)

    # Fee estimate + Polygon gas for the PM leg
    fees = _select_fees(case1_fees, case2_fees, poly_price)
    gas = POLYGON_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": fees + gas,
        "net_profit": gross_spread - fees - gas,
    }


# ---------------------------------------------------------------------------
# SX Bet fee calculations
# ---------------------------------------------------------------------------

def net_profit_sxbet_backall(implied_probs: list[float]) -> dict:
    """Calculate net profit for SX Bet back-all arbitrage.

    SX Bet has 0% commission on API trades -- no commission on winnings.
    """
    total_cost = sum(implied_probs)
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    return {
        "gross_spread": gross_spread,
        "fees": 0,
        "net_profit": gross_spread,
    }


def net_profit_sxbet_backlay(back_price: float, lay_price: float) -> dict:
    """Calculate net profit for SX Bet back-lay arbitrage. 0% fees."""
    if lay_price <= back_price:
        return {"gross_spread": 0, "fees": 0, "net_profit": 0}

    gross = lay_price - back_price
    return {
        "gross_spread": gross,
        "fees": 0,
        "net_profit": gross,
    }


# ---------------------------------------------------------------------------
# Matchbook fee calculations (0% commission on prediction markets)
# ---------------------------------------------------------------------------

def net_profit_matchbook_backall(implied_probs: list[float]) -> dict:
    """Calculate net profit for Matchbook back-all arbitrage.

    Matchbook has 0% commission on prediction markets — pure overround arb.
    """
    total_cost = sum(implied_probs)
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    return {
        "gross_spread": gross_spread,
        "fees": 0,
        "net_profit": gross_spread,
    }


def net_profit_matchbook_backlay(back_price: float, lay_price: float) -> dict:
    """Calculate net profit for Matchbook back-lay arbitrage. 0% commission."""
    if lay_price <= back_price:
        return {"gross_spread": 0, "fees": 0, "net_profit": 0}

    gross = lay_price - back_price
    return {
        "gross_spread": gross,
        "fees": 0,
        "net_profit": gross,
    }


def net_profit_cross_matchbook(
    poly_price: float,
    mb_price: float,
    poly_side: str,
    mb_side: str,
) -> dict:
    """Calculate net profit for cross-platform arbitrage: Polymarket vs Matchbook.

    poly_side/mb_side: 'yes' or 'no' -- what we're buying on each platform.
    One of the two positions will win $1.00, the other $0.00.
    Matchbook has 0% commission.
    """
    total_cost = poly_price + mb_price

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    # Polymarket leg pays entry fee at trade time (March 2026 model); Matchbook 0% commission
    fees = polymarket_taker_fee(poly_price)
    gas = POLYGON_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": fees + gas,
        "net_profit": gross_spread - fees - gas,
    }


# ---------------------------------------------------------------------------
# Gemini Predictions fee calculations
# Fee formula: min(P, 1-P) * quantity * fee_rate per contract
# Default: 5% taker (IOC), 1% maker (GTC)
# ---------------------------------------------------------------------------

def gemini_fee(price: float, fee_rate: float | None = None, contracts: int = 1) -> float:
    """Calculate Gemini fee for a single contract.

    Formula (March 18, 2026): fee_rate * C * P * (1 - P). Rounded up to next cent.
    Default taker rate 0.07 (7%).
    Old formula was min(P, 1-P) * fee_rate — replaced by P*(1-P)*rate.

    Args:
        price: Trade price in [0, 1].
        fee_rate: Override fee rate; uses GEMINI_TAKER_RATE if None.
        contracts: Number of contracts (default 1).

    Returns:
        Total fee in dollars, rounded up to next cent.
    """
    if price <= 0 or price >= 1:
        return 0.0
    rate = fee_rate if fee_rate is not None else GEMINI_TAKER_RATE
    raw = rate * contracts * price * (1.0 - price)
    return math.ceil(raw * 100) / 100


def net_profit_gemini_binary(yes_price: float, no_price: float,
                             fee_rate: float | None = None) -> dict:
    """Calculate net profit for a Gemini binary arbitrage.

    Buy YES + NO. One always pays $1.00.
    Each leg pays Gemini fee at entry using the 2026 formula: rate * P * (1-P).
    """
    total_cost = yes_price + no_price
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    fees = gemini_fee(yes_price, fee_rate) + gemini_fee(no_price, fee_rate)

    return {
        "gross_spread": gross_spread,
        "fees": fees,
        "net_profit": gross_spread - fees,
    }


def net_profit_gemini_multi(yes_prices: list[float], fee_rate: float | None = None) -> dict:
    """Calculate net profit for a Gemini categorical (multi-outcome) arbitrage.

    Buy YES on each outcome. Exactly one pays $1.00.
    Each leg pays Gemini fee at entry using the 2026 formula: rate * P * (1-P).
    """
    total_cost = sum(yes_prices)
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    fees = sum(gemini_fee(p, fee_rate) for p in yes_prices)

    return {
        "gross_spread": gross_spread,
        "fees": fees,
        "net_profit": gross_spread - fees,
    }


def net_profit_cross_gemini(
    poly_price: float,
    gm_price: float,
    poly_side: str,
    gm_side: str,
    fee_rate: float | None = None,
) -> dict:
    """Calculate net profit for cross-platform arbitrage: Polymarket vs Gemini.

    poly_side/gm_side: 'yes' or 'no' -- what we're buying on each platform.
    One of the two positions will win $1.00, the other $0.00.
    Both legs pay entry-time fees using the 2026 formula.
    """
    total_cost = poly_price + gm_price

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    # Both legs pay entry fees at trade time (March 2026 model)
    pm_entry_fee = polymarket_taker_fee(poly_price)
    gm_entry_fee = gemini_fee(gm_price, fee_rate)

    # Both fees are charged at entry regardless of outcome
    fees = pm_entry_fee + gm_entry_fee
    gas = POLYGON_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": fees + gas,
        "net_profit": gross_spread - fees - gas,
    }


# ---------------------------------------------------------------------------
# IBKR ForecastEx fee calculations ($0.00 commission)
# ---------------------------------------------------------------------------

def net_profit_ibkr_binary(yes_price: float, no_price: float) -> dict:
    """Calculate net profit for an IBKR ForecastEx binary arbitrage.

    BUY YES + BUY NO (both are buy orders). One pays $1, other $0.
    IBKR has $0.00 commission on ForecastEx.
    """
    total_cost = yes_price + no_price
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    return {
        "gross_spread": gross_spread,
        "fees": 0,
        "net_profit": gross_spread,
    }


def net_profit_cross_ibkr(
    poly_price: float,
    ibkr_price: float,
    poly_side: str,
    ibkr_side: str,
) -> dict:
    """Calculate net profit for cross-platform arbitrage: Polymarket vs IBKR.

    poly_side/ibkr_side: 'yes' or 'no' -- what we're buying on each platform.
    One of the two positions will win $1.00, the other $0.00.
    IBKR has $0.00 commission.
    """
    total_cost = poly_price + ibkr_price

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    # Polymarket leg pays entry fee at trade time; IBKR has $0.00 commission
    fees = polymarket_taker_fee(poly_price)
    gas = POLYGON_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": fees + gas,
        "net_profit": gross_spread - fees - gas,
    }


def net_profit_triangular(
    yes_price: float,
    no_price: float,
    yes_platform: str,
    no_platform: str,
) -> dict:
    """Calculate net profit for a triangular (3+ platform) cross-platform arbitrage.

    Buys YES on yes_platform and NO on no_platform. One position always wins $1.00.
    Fee calculation picks the worst-case fee scenario for each platform's fee model.

    Args:
        yes_price: Price of the YES contract on the best-YES platform.
        no_price: Price of the NO contract on the best-NO platform.
        yes_platform: Name of the platform where YES is bought.
        no_platform: Name of the platform where NO is bought.

    Returns:
        Dict with gross_spread, fees, and net_profit.
    """
    total_cost = yes_price + no_price

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    # Compute worst-case fees for the YES-side platform
    yes_fee = _platform_win_fee(yes_price, yes_platform)
    # Compute worst-case fees for the NO-side platform
    no_fee = _platform_win_fee(no_price, no_platform)

    # Entry fees (paid regardless of outcome)
    yes_entry = _platform_entry_fee(yes_price, yes_platform)
    no_entry = _platform_entry_fee(no_price, no_platform)
    entry_fees = yes_entry + no_entry

    # If YES wins: YES-side win fee + NO-side entry fee
    case_yes_wins = yes_fee + entry_fees
    # If NO wins: NO-side win fee + YES-side entry fee
    case_no_wins = no_fee + entry_fees

    fees = _select_fees(case_yes_wins, case_no_wins, yes_price)

    # Gas: Polygon gas for any Polymarket leg
    gas = 0.0
    if yes_platform == "polymarket" or no_platform == "polymarket":
        gas = POLYGON_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": fees + gas,
        "net_profit": gross_spread - fees - gas,
    }


def _platform_win_fee(price: float, platform: str) -> float:
    """Calculate the winner fee for a platform (fee charged when position pays out $1).

    March 2026: Polymarket no longer charges on winnings — fee is at entry.
    """
    if platform == "polymarket":
        # Polymarket switched to entry-time fee in March 2026; no settlement fee
        return 0.0
    elif platform == "kalshi":
        # Kalshi taker fee is an entry fee, not a win fee
        return 0.0
    elif platform == "betfair":
        return betfair_commission(1.0 - price)
    elif platform == "smarkets":
        return smarkets_commission(1.0 - price)
    # sxbet, matchbook, gemini (entry fee), ibkr: no win fee
    return 0.0


def _platform_entry_fee(price: float, platform: str) -> float:
    """Calculate the entry fee for a platform (fee charged when placing the trade)."""
    if platform == "polymarket":
        return polymarket_taker_fee(price)
    elif platform == "kalshi":
        return kalshi_taker_fee(price)
    elif platform == "gemini":
        return gemini_fee(price)
    # betfair, smarkets: charge on winnings (win fee), not at entry
    # sxbet, matchbook, ibkr: 0% fees
    return 0.0


def net_profit_cross_generic(
    price_a: float,
    price_b: float,
    side_a: str,
    side_b: str,
    platform_a: str = "",
    platform_b: str = "",
) -> dict:
    """Calculate net profit for a cross-platform arbitrage between any two platforms.

    Uses _platform_win_fee and _platform_entry_fee to compute worst-case fees
    for the given platform pair. Includes Polygon gas when either platform is
    Polymarket.

    Args:
        price_a: Price on platform A.
        price_b: Price on platform B.
        side_a: 'yes' or 'no' — what we're buying on platform A.
        side_b: 'yes' or 'no' — what we're buying on platform B.
        platform_a: Name of the first platform.
        platform_b: Name of the second platform.

    Returns:
        Dict with gross_spread, fees, and net_profit.
    """
    total_cost = price_a + price_b

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    # Entry fees (paid regardless of outcome)
    entry_a = _platform_entry_fee(price_a, platform_a)
    entry_b = _platform_entry_fee(price_b, platform_b)
    entry_fees = entry_a + entry_b

    # Case 1 (A wins): A's win fee + B's entry fee + A's entry fee
    case1_fees = _platform_win_fee(price_a, platform_a) + entry_fees
    # Case 2 (B wins): B's win fee + A's entry fee + B's entry fee
    case2_fees = _platform_win_fee(price_b, platform_b) + entry_fees

    fees = _select_fees(case1_fees, case2_fees, price_a)

    # Polygon gas when one side is Polymarket
    gas = 0.0
    if platform_a == "polymarket" or platform_b == "polymarket":
        gas = POLYGON_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": fees + gas,
        "net_profit": gross_spread - fees - gas,
    }


def net_profit_cross_sxbet(
    poly_price: float,
    sx_price: float,
    poly_side: str,
    sx_side: str,
) -> dict:
    """Calculate net profit for cross-platform arbitrage: Polymarket vs SX Bet.

    poly_side/sx_side: 'yes' or 'no' -- what we're buying on each platform.
    One of the two positions will win $1.00, the other $0.00.
    """
    total_cost = poly_price + sx_price

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    # Polymarket leg pays entry fee at trade time; SX Bet has 0% fees
    fees = polymarket_taker_fee(poly_price)
    gas = POLYGON_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": fees + gas,
        "net_profit": gross_spread - fees - gas,
    }


# ---------------------------------------------------------------------------
# Multi-outcome cross-platform fee calculation
# ---------------------------------------------------------------------------

def net_profit_multi_cross(
    outcome_prices: list[float],
    outcome_platforms: list[str],
) -> dict:
    """Calculate net profit for a multi-outcome cross-platform arbitrage.

    Buys YES on each outcome on potentially different platforms.  Exactly one
    outcome wins and pays $1.  The arb is profitable when the total cost of
    buying YES on every outcome (across the cheapest platforms) is less than
    $1 minus all fees.

    Fees are computed per-outcome:
    - Entry fees are always charged (platform-specific).
    - Win fee is charged only on the outcome that settles YES.  Since we
      don't know which one wins, we take the worst-case (highest) win fee
      across all outcomes.

    Gas: one POLYGON_GAS_ESTIMATE is charged if any leg is on Polymarket.

    Args:
        outcome_prices: YES price for each outcome (one per outcome).
        outcome_platforms: Platform name for each outcome (parallel list).

    Returns:
        Dict with gross_spread, fees, and net_profit.
    """
    total_cost = sum(outcome_prices)

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    # Entry fees: always paid on every leg regardless of outcome
    entry_fees = sum(
        _platform_entry_fee(p, plat)
        for p, plat in zip(outcome_prices, outcome_platforms)
    )

    # Win fee: depends on which outcome wins — take worst case
    win_fees_per_outcome = [
        _platform_win_fee(p, plat)
        for p, plat in zip(outcome_prices, outcome_platforms)
    ]
    worst_win_fee = max(win_fees_per_outcome) if win_fees_per_outcome else 0.0

    fees = entry_fees + worst_win_fee

    # Gas: one charge if any leg touches Polymarket
    gas = POLYGON_GAS_ESTIMATE if "polymarket" in outcome_platforms else 0.0

    return {
        "gross_spread": gross_spread,
        "fees": fees + gas,
        "net_profit": gross_spread - fees - gas,
    }


# ---------------------------------------------------------------------------
# Dynamic fee routing — pick lowest-fee path for cross-platform opportunities
# ---------------------------------------------------------------------------

# Real-time fee schedule per platform (can be updated at runtime for promos)
# Real-time fee schedule per platform (2026 rates; can be updated at runtime for promos).
# taker/maker values are fee RATES used by estimate_total_fee() for routing decisions.
# Actual fee amounts use the dedicated fee functions (polymarket_taker_fee, etc.).
PLATFORM_FEE_SCHEDULE: dict[str, dict[str, float]] = {
    "polymarket": {"taker": 0.04, "maker": 0.00, "gas": POLYGON_GAS_ESTIMATE},
    "kalshi": {"taker": 0.07, "maker": 0.0175, "gas": 0.0},
    "betfair": {"taker": 0.05, "maker": 0.05, "gas": 0.0},
    "smarkets": {"taker": 0.02, "maker": 0.02, "gas": 0.0},
    "sxbet": {"taker": 0.00, "maker": 0.00, "gas": 0.0},
    "matchbook": {"taker": 0.00, "maker": 0.00, "gas": 0.0},
    "gemini": {"taker": 0.07, "maker": 0.0175, "gas": 0.0},
    "ibkr": {"taker": 0.00, "maker": 0.00, "gas": 0.0},
}


def estimate_total_fee(platform: str, price: float, order_type: str = "taker") -> float:
    """Estimate total fee for a trade on a platform.

    Args:
        platform: Platform name.
        price: Trade price (0-1).
        order_type: "taker" or "maker".

    Returns:
        Estimated fee in dollars per contract.
    """
    schedule = PLATFORM_FEE_SCHEDULE.get(platform, {})
    fee_rate = schedule.get(order_type, 0.0)
    gas = schedule.get("gas", 0.0)

    if platform == "polymarket":
        # March 2026: entry fee only (no settlement fee)
        if order_type == "taker":
            return polymarket_taker_fee(price) + gas
        else:
            return gas  # Maker pays 0%
    elif platform == "kalshi":
        if order_type == "taker":
            return kalshi_taker_fee(price)
        else:
            return kalshi_maker_fee(price)
    elif platform == "gemini":
        if order_type == "taker":
            return gemini_fee(price, GEMINI_TAKER_RATE) + gas
        else:
            return gemini_fee(price, GEMINI_MAKER_RATE) + gas
    elif platform in ("betfair", "smarkets"):
        # Commission on winnings
        return (1.0 - price) * fee_rate
    else:
        return gas


def find_lowest_fee_path(
    platforms: list[str],
    yes_prices: dict[str, float],
    no_prices: dict[str, float],
) -> dict | None:
    """Find the lowest-fee cross-platform path for an arb opportunity.

    Given YES and NO prices across multiple platforms, finds the pair
    (buy YES on platform A, buy NO on platform B) that minimizes total fees.

    Args:
        platforms: List of platform names with prices.
        yes_prices: {platform: yes_ask_price} for each platform.
        no_prices: {platform: no_ask_price} for each platform.

    Returns:
        Dict with best_yes_platform, best_no_platform, total_cost,
        estimated_fees, net_profit, or None if no profitable path exists.
    """
    best = None

    for yes_plat in platforms:
        yes_p = yes_prices.get(yes_plat)
        if yes_p is None or yes_p <= 0 or yes_p >= 1:
            continue
        for no_plat in platforms:
            if no_plat == yes_plat:
                continue
            no_p = no_prices.get(no_plat)
            if no_p is None or no_p <= 0 or no_p >= 1:
                continue

            total_cost = yes_p + no_p
            if total_cost >= 1.0:
                continue

            # Estimate fees for this path
            yes_fee = estimate_total_fee(yes_plat, yes_p)
            no_fee = estimate_total_fee(no_plat, no_p)
            total_fees = yes_fee + no_fee
            net_profit = 1.0 - total_cost - total_fees

            if net_profit <= 0:
                continue

            if best is None or net_profit > best["net_profit"]:
                best = {
                    "best_yes_platform": yes_plat,
                    "best_no_platform": no_plat,
                    "yes_price": yes_p,
                    "no_price": no_p,
                    "total_cost": total_cost,
                    "estimated_fees": total_fees,
                    "net_profit": net_profit,
                }

    return best


def net_profit_rewards(bid_price: float, ask_price: float, size: float = 1.0,
                       platform: str = "polymarket") -> dict:
    """Calculate net profit for reward resting orders.

    For rewards strategy, profit comes from two sources:
    1. Spread capture (bid/ask spread on fills)
    2. Reward payout (tracked separately in database)

    This function calculates the spread profit per fill. Actual reward yield
    is tracked separately via RewardTracker/KalshiRewardTracker in database.

    Args:
        bid_price: Resting bid price (0-1).
        ask_price: Resting ask price (0-1).
        size: Order size in dollars.
        platform: "polymarket" or "kalshi".

    Returns:
        Dict with net_profit, spread, fees, net_roi, bid, ask keys.
    """
    mid = (bid_price + ask_price) / 2
    spread = ask_price - bid_price

    # Platform fees for resting limit orders (makers)
    if platform == "polymarket":
        # Polymarket maker fee: 0% for most cases, but conservative estimate of 0.5%
        # in case of fee structures we're not aware of
        fee_rate = 0.005
    elif platform == "kalshi":
        # Kalshi maker fee: lower than taker; use conservative 0.5% estimate
        # Actual: ceil(KALSHI_MAKER_MULTIPLIER * P * (1 - P)) in cents
        fee_rate = 0.005
    else:
        # Default conservative fee rate
        fee_rate = 0.01

    fees = spread * size * fee_rate
    net_profit = spread * size - fees
    net_roi = (net_profit / size) * 100 if size > 0 else 0.0

    return {
        "net_profit": net_profit,
        "spread": spread,
        "fees": fees,
        "net_roi": net_roi,
        "bid": bid_price,
        "ask": ask_price,
    }


# ---------------------------------------------------------------------------
# Order book imbalance fee calculations (Layer 4 - Informed Trading)
# ---------------------------------------------------------------------------

def net_profit_imbalance(
    entry_price: float,
    exit_price: float,
    size: float,
    platform: str = "polymarket",
) -> float:
    """Calculate net profit for an order book imbalance signal execution.

    Imbalance trades are Layer 4 (informed trading) based on directional signals
    from bid/ask volume ratios. Execution uses taker orders (time-sensitive) because
    the signal may decay quickly. Entry and exit both incur taker fees.

    Args:
        entry_price: Entry price in [0, 1] (where we buy the predicted direction).
        exit_price: Exit price in [0, 1] (where we sell to lock in profit).
        size: Trade size in dollars.
        platform: Platform for fee calculation ("polymarket", "kalshi", "gemini", etc.).

    Returns:
        Net profit in USD after fees. May be negative if signal was wrong.
    """
    if size <= 0:
        return 0.0

    if platform == "polymarket":
        # Polymarket: both entry and exit pay taker fee at trade time
        # Fee formula: POLYMARKET_DEFAULT_TAKER_RATE * size * price * (1 - price)
        entry_fee = polymarket_taker_fee(entry_price, contracts=1) * size
        exit_fee = polymarket_taker_fee(exit_price, contracts=1) * size
        gas = POLYGON_GAS_ESTIMATE * 2  # Two Polygon transactions
        gross_profit = size * (exit_price - entry_price)
        net_profit = gross_profit - entry_fee - exit_fee - gas

    elif platform == "kalshi":
        # Kalshi: both entry and exit pay taker fee
        # Fee formula: ceil(0.07 * price * (1 - price)) per contract in cents
        entry_fee = kalshi_taker_fee(entry_price, contracts=1) * size
        exit_fee = kalshi_taker_fee(exit_price, contracts=1) * size
        gross_profit = size * (exit_price - entry_price)
        net_profit = gross_profit - entry_fee - exit_fee

    elif platform == "gemini":
        # Gemini: 5% taker fee (or GEMINI_TAKER_RATE if defined)
        # Fee = min(price, 1 - price) * fee_rate
        entry_fee = min(entry_price, 1.0 - entry_price) * GEMINI_TAKER_RATE * size
        exit_fee = min(exit_price, 1.0 - exit_price) * GEMINI_TAKER_RATE * size
        gross_profit = size * (exit_price - entry_price)
        net_profit = gross_profit - entry_fee - exit_fee

    else:
        # Default: use conservative taker fee estimate (1%)
        fee_rate = 0.01
        entry_fee = entry_price * fee_rate * size
        exit_fee = exit_price * fee_rate * size
        gross_profit = size * (exit_price - entry_price)
        net_profit = gross_profit - entry_fee - exit_fee

    return net_profit

