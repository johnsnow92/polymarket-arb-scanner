"""Fee calculators for Polymarket and Kalshi."""

import math

from config import (
    KALSHI_FEE_CAP_CENTS,
    POLYGON_GAS_ESTIMATE,
    BNB_GAS_ESTIMATE,
    SOLANA_GAS_ESTIMATE,
    BASE_GAS_ESTIMATE,
)


def polymarket_fee(buy_price: float, sell_price: float = 1.0) -> float:
    """Calculate Polymarket fee on a winning position.

    Polymarket charges 0% trading fee + 2% on net winnings.
    Net winnings = payout - cost = sell_price - buy_price.
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


def net_profit_binary_internal(yes_price: float, no_price: float) -> dict:
    """Calculate net profit for a Polymarket binary arbitrage.

    Buy YES + NO. One always pays $1.00.
    Profit = $1.00 - (yes_price + no_price) - fees.
    """
    total_cost = yes_price + no_price
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    # The winning side pays $1.00. Fee is 2% of net winnings on that side.
    # Worst case: the cheaper side wins -> higher net winnings -> higher fee.
    cheaper = min(yes_price, no_price)
    fee = polymarket_fee(cheaper, 1.0)

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
    Profit = $1.00 - sum(prices) - fee_on_winner.
    """
    total_cost = sum(yes_prices)
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    # Winner fee: 2% of (1.0 - winning_price). Worst case = cheapest outcome wins.
    cheapest = min(yes_prices)
    fee = polymarket_fee(cheapest, 1.0)

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

    # Kalshi taker fee is paid on entry regardless of outcome
    kalshi_entry_fee = kalshi_taker_fee(kalshi_price, 1)

    # Fees depend on which side wins:
    # Case 1 (Poly wins): PM 2% winner fee + Kalshi entry fee
    case1_fees = polymarket_fee(poly_price, 1.0) + kalshi_entry_fee
    # Case 2 (Kalshi wins): only Kalshi entry fee (PM side loses, no fee)
    case2_fees = kalshi_entry_fee

    # Use worst-case fees + Polygon gas for the PM leg
    worst_fees = max(case1_fees, case2_fees)
    gas = POLYGON_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": worst_fees + gas,
        "net_profit": gross_spread - worst_fees - gas,
    }


# ---------------------------------------------------------------------------
# PredictIt fee calculations
# ---------------------------------------------------------------------------

def predictit_fee(profit: float) -> float:
    """PredictIt takes 10% of profits, 5% withdrawal fee.

    The 10% profit fee is deducted at settlement. The 5% withdrawal fee
    applies when withdrawing funds and is handled separately.
    """
    if profit <= 0:
        return 0.0
    return profit * 0.10


def net_profit_cross_predictit(
    poly_price: float,
    pi_price: float,
    poly_side: str,
    pi_side: str,
) -> dict:
    """Calculate net profit for cross-platform arbitrage: Polymarket vs PredictIt.

    poly_side/pi_side: 'yes' or 'no' -- what we're buying on each platform.
    One of the two positions will win $1.00, the other $0.00.
    """
    total_cost = poly_price + pi_price

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    # Case 1 (Poly wins): Polymarket 2% winner fee + no PredictIt fee (PI side lost)
    poly_win_profit = 1.0 - poly_price
    case1_fees = polymarket_fee(poly_price, 1.0)

    # Case 2 (PredictIt wins): PredictIt 10% profit fee + no PM fee (PM side lost)
    pi_win_profit = 1.0 - pi_price
    case2_fees = predictit_fee(pi_win_profit)

    # Use worst-case fees + Polygon gas for the PM leg
    worst_fees = max(case1_fees, case2_fees)
    gas = POLYGON_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": worst_fees + gas,
        "net_profit": gross_spread - worst_fees - gas,
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

    # Case 1 (Poly wins): PM 2% winner fee + no Betfair commission (BF side lost)
    case1_fees = polymarket_fee(poly_price, 1.0)

    # Case 2 (Betfair wins): Betfair commission on net winnings + no PM fee
    bf_win_profit = 1.0 - bf_price
    case2_fees = betfair_commission(bf_win_profit, commission_rate)

    # Use worst-case fees + Polygon gas for the PM leg
    worst_fees = max(case1_fees, case2_fees)
    gas = POLYGON_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": worst_fees + gas,
        "net_profit": gross_spread - worst_fees - gas,
    }


# ---------------------------------------------------------------------------
# Manifold fee calculations
# ---------------------------------------------------------------------------

def manifold_fee(amount: float) -> float:
    """Manifold has no trading fees."""
    return 0.0


def net_profit_cross_manifold(
    poly_price: float,
    mf_price: float,
    poly_side: str,
    mf_side: str,
) -> dict:
    """Calculate net profit for cross-platform arbitrage: Polymarket vs Manifold.

    poly_side/mf_side: 'yes' or 'no' -- what we're buying on each platform.
    One of the two positions will win $1.00, the other $0.00.

    Note: Manifold uses mana (play money) for most markets. Cross-platform
    arb is only meaningful for sweepstakes markets with real-money payouts.
    """
    total_cost = poly_price + mf_price

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    # Manifold has no fees, so only PM fees apply when PM side wins
    # Case 1 (Poly wins): PM 2% winner fee
    case1_fees = polymarket_fee(poly_price, 1.0)
    # Case 2 (Manifold wins): no fees on either side
    case2_fees = 0.0

    worst_fees = max(case1_fees, case2_fees)
    gas = POLYGON_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": worst_fees + gas,
        "net_profit": gross_spread - worst_fees - gas,
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


def net_profit_spread_kalshi(ask: float, bid: float) -> dict:
    """Calculate net profit for a Kalshi spread capture (buy at ask, sell at bid).

    Both buy and sell pay taker fees.
    """
    if bid <= ask:
        return {"gross_spread": bid - ask, "fees": 0, "net_profit": bid - ask}

    gross = bid - ask
    fees = kalshi_taker_fee(ask) + kalshi_taker_fee(bid)
    return {
        "gross_spread": gross,
        "fees": fees,
        "net_profit": gross - fees,
    }


# ---------------------------------------------------------------------------
# PredictIt standalone fee calculations
# ---------------------------------------------------------------------------

def net_profit_predictit_binary(yes_price: float, no_price: float) -> dict:
    """Calculate net profit for a PredictIt binary arbitrage (buy YES + NO).

    PredictIt charges 10% on profits at settlement. Buy both sides for < $1.
    """
    total_cost = yes_price + no_price
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    # Winner's profit: $1 - winning_side_price. 10% fee on that profit.
    # Worst case: cheapest side wins (highest profit, highest fee).
    cheapest = min(yes_price, no_price)
    fee = predictit_fee(1.0 - cheapest)

    return {
        "gross_spread": gross_spread,
        "fees": fee,
        "net_profit": gross_spread - fee,
    }


def net_profit_predictit_multi(yes_prices: list[float]) -> dict:
    """Calculate net profit for a PredictIt multi-outcome arbitrage.

    Buy YES on every contract in a market. Exactly one pays $1.
    10% profit fee on the winner.
    """
    total_cost = sum(yes_prices)
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    cheapest = min(yes_prices)
    fee = predictit_fee(1.0 - cheapest)

    return {
        "gross_spread": gross_spread,
        "fees": fee,
        "net_profit": gross_spread - fee,
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

    # Case 1 (Poly wins): PM 2% winner fee + no Smarkets commission (SM side lost)
    case1_fees = polymarket_fee(poly_price, 1.0)

    # Case 2 (Smarkets wins): Smarkets commission on net winnings + no PM fee
    sm_win_profit = 1.0 - sm_price
    case2_fees = smarkets_commission(sm_win_profit, commission_rate)

    # Use worst-case fees + Polygon gas for the PM leg
    worst_fees = max(case1_fees, case2_fees)
    gas = POLYGON_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": worst_fees + gas,
        "net_profit": gross_spread - worst_fees - gas,
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

    # Case 1 (Poly wins): PM 2% winner fee
    case1_fees = polymarket_fee(poly_price, 1.0)
    # Case 2 (SX Bet wins): no fees on either side
    case2_fees = 0.0

    worst_fees = max(case1_fees, case2_fees)
    gas = POLYGON_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": worst_fees + gas,
        "net_profit": gross_spread - worst_fees - gas,
    }


# ---------------------------------------------------------------------------
# Opinion fee calculations (BNB Chain)
# ---------------------------------------------------------------------------

def net_profit_opinion_binary(yes_price: float, no_price: float) -> dict:
    """Calculate net profit for an Opinion binary arbitrage.

    Opinion has ~0% trading fees. Only BNB gas costs.
    """
    total_cost = yes_price + no_price
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    gas = BNB_GAS_ESTIMATE * 2  # Two transactions
    return {
        "gross_spread": gross_spread,
        "fees": gas,
        "net_profit": gross_spread - gas,
    }


def net_profit_opinion_multi(yes_prices: list[float]) -> dict:
    """Calculate net profit for an Opinion multi-outcome arbitrage."""
    total_cost = sum(yes_prices)
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    gas = BNB_GAS_ESTIMATE * len(yes_prices)
    return {
        "gross_spread": gross_spread,
        "fees": gas,
        "net_profit": gross_spread - gas,
    }


def net_profit_cross_opinion(
    poly_price: float,
    op_price: float,
    poly_side: str,
    op_side: str,
) -> dict:
    """Calculate net profit for cross-platform arbitrage: Polymarket vs Opinion."""
    total_cost = poly_price + op_price

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    case1_fees = polymarket_fee(poly_price, 1.0)
    case2_fees = 0  # Opinion has no winner fee

    worst_fees = max(case1_fees, case2_fees)
    gas = POLYGON_GAS_ESTIMATE + BNB_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": worst_fees + gas,
        "net_profit": gross_spread - worst_fees - gas,
    }


# ---------------------------------------------------------------------------
# Drift BET fee calculations (Solana)
# ---------------------------------------------------------------------------

def net_profit_drift_binary(yes_price: float, no_price: float) -> dict:
    """Calculate net profit for a Drift BET binary arbitrage.

    Drift has ~0% trading fees. Only SOL gas costs.
    """
    total_cost = yes_price + no_price
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    gas = SOLANA_GAS_ESTIMATE * 2
    return {
        "gross_spread": gross_spread,
        "fees": gas,
        "net_profit": gross_spread - gas,
    }


def net_profit_cross_drift(
    poly_price: float,
    drift_price: float,
    poly_side: str,
    drift_side: str,
) -> dict:
    """Calculate net profit for cross-platform arbitrage: Polymarket vs Drift."""
    total_cost = poly_price + drift_price

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    case1_fees = polymarket_fee(poly_price, 1.0)
    case2_fees = 0

    worst_fees = max(case1_fees, case2_fees)
    gas = POLYGON_GAS_ESTIMATE + SOLANA_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": worst_fees + gas,
        "net_profit": gross_spread - worst_fees - gas,
    }


# ---------------------------------------------------------------------------
# Limitless fee calculations (Base Chain)
# ---------------------------------------------------------------------------

def limitless_dynamic_fee(profit: float, days_to_resolution: float = 7.0) -> float:
    """Limitless dynamic fee: 0.03%-3% based on time to resolution.

    Shorter resolution = lower fee. Longer = higher.
    Scale: <1 day = 0.03%, 1-7 days = ~0.5%, 7-30 days = ~1.5%, 30+ days = 3%
    """
    if profit <= 0:
        return 0.0

    if days_to_resolution <= 1:
        rate = 0.0003
    elif days_to_resolution <= 7:
        rate = 0.0003 + (days_to_resolution - 1) * (0.005 - 0.0003) / 6
    elif days_to_resolution <= 30:
        rate = 0.005 + (days_to_resolution - 7) * (0.015 - 0.005) / 23
    else:
        rate = min(0.03, 0.015 + (days_to_resolution - 30) * 0.0005)

    return profit * rate


def net_profit_limitless_binary(yes_price: float, no_price: float, days_to_resolution: float = 7.0) -> dict:
    """Calculate net profit for a Limitless binary arbitrage.

    Limitless has dynamic fees based on days to resolution + Base gas.
    """
    total_cost = yes_price + no_price
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    fee = limitless_dynamic_fee(gross_spread, days_to_resolution)
    gas = BASE_GAS_ESTIMATE * 2

    return {
        "gross_spread": gross_spread,
        "fees": fee + gas,
        "net_profit": gross_spread - fee - gas,
    }


def net_profit_cross_limitless(
    poly_price: float,
    lim_price: float,
    poly_side: str,
    lim_side: str,
    days_to_resolution: float = 7.0,
) -> dict:
    """Calculate net profit for cross-platform arbitrage: Polymarket vs Limitless."""
    total_cost = poly_price + lim_price

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    case1_fees = polymarket_fee(poly_price, 1.0)
    case2_fees = limitless_dynamic_fee(1.0 - lim_price, days_to_resolution)

    worst_fees = max(case1_fees, case2_fees)
    gas = POLYGON_GAS_ESTIMATE + BASE_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": worst_fees + gas,
        "net_profit": gross_spread - worst_fees - gas,
    }


# ---------------------------------------------------------------------------
# ForecastEx (IBKR) fee calculations
# ---------------------------------------------------------------------------

def forecastex_commission(num_contracts: int = 2, commission_per_contract: float = 0.01) -> float:
    """ForecastEx/IBKR commission: flat fee per contract.

    Default $0.01 per contract. Both legs pay commission.
    """
    return num_contracts * commission_per_contract


def net_profit_forecastex_binary(yes_price: float, no_price: float, commission_per_contract: float = 0.01) -> dict:
    """Calculate net profit for a ForecastEx binary arbitrage.

    Buy YES + Buy NO (can't sell contracts — must buy opposing side).
    Both legs pay IBKR commission.
    """
    total_cost = yes_price + no_price
    gross_spread = 1.0 - total_cost

    if gross_spread <= 0:
        return {"gross_spread": gross_spread, "fees": 0, "net_profit": gross_spread}

    # Commission on both legs
    fees = forecastex_commission(2, commission_per_contract)

    return {
        "gross_spread": gross_spread,
        "fees": fees,
        "net_profit": gross_spread - fees,
    }


def net_profit_cross_forecastex(
    poly_price: float,
    fx_price: float,
    poly_side: str,
    fx_side: str,
    commission_per_contract: float = 0.01,
) -> dict:
    """Calculate net profit for cross-platform arbitrage: Polymarket vs ForecastEx."""
    total_cost = poly_price + fx_price

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    # Case 1 (Poly wins): PM 2% winner fee + IBKR commission on FX leg
    case1_fees = polymarket_fee(poly_price, 1.0) + forecastex_commission(1, commission_per_contract)

    # Case 2 (ForecastEx wins): IBKR commission on FX leg (PM side lost, no PM fee)
    case2_fees = forecastex_commission(1, commission_per_contract)

    worst_fees = max(case1_fees, case2_fees)
    gas = POLYGON_GAS_ESTIMATE  # Only PM side has gas

    return {
        "gross_spread": gross_spread,
        "fees": worst_fees + gas,
        "net_profit": gross_spread - worst_fees - gas,
    }
