"""Fee calculators for Polymarket and Kalshi."""

import math

from config import KALSHI_FEE_CAP_CENTS, POLYGON_GAS_ESTIMATE


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
