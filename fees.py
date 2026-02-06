"""Fee calculators for Polymarket and Kalshi."""

import math


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
    # Cap at 1.75 cents (high-volume tier) -- use conservative estimate
    fee_cents = min(fee_cents, 175)  # 1.75 * 100 = no real cap for retail
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

    return {
        "gross_spread": gross_spread,
        "fees": fee,
        "net_profit": gross_spread - fee,
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

    return {
        "gross_spread": gross_spread,
        "fees": fee,
        "net_profit": gross_spread - fee,
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

    # Use worst-case fees
    worst_fees = max(case1_fees, case2_fees)

    return {
        "gross_spread": gross_spread,
        "fees": worst_fees,
        "net_profit": gross_spread - worst_fees,
    }
