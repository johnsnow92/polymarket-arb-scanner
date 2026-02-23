"""Fee calculators for Polymarket and Kalshi."""

import logging
import math

from config import (
    FEE_MODEL,
    KALSHI_FEE_CAP_CENTS,
    POLYGON_GAS_ESTIMATE,
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

    # Fee estimate + Polygon gas for the PM leg
    fees = _select_fees(case1_fees, case2_fees, poly_price)
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

    # Case 1 (Poly wins): PM 2% winner fee + no Betfair commission (BF side lost)
    case1_fees = polymarket_fee(poly_price, 1.0)

    # Case 2 (Betfair wins): Betfair commission on net winnings + no PM fee
    bf_win_profit = 1.0 - bf_price
    case2_fees = betfair_commission(bf_win_profit, commission_rate)

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

    # Case 1 (Poly wins): PM 2% winner fee
    case1_fees = polymarket_fee(poly_price, 1.0)
    # Case 2 (Matchbook wins): no fees on either side
    case2_fees = 0.0

    fees = _select_fees(case1_fees, case2_fees, poly_price)
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

def gemini_fee(price: float, fee_rate: float = 0.05) -> float:
    """Calculate Gemini fee for a single contract.

    Formula: min(P, 1-P) * fee_rate.
    Default 5% is the taker rate (IOC orders).
    """
    if price <= 0 or price >= 1:
        return 0.0
    return min(price, 1.0 - price) * fee_rate


def net_profit_gemini_binary(yes_price: float, no_price: float, fee_rate: float = 0.05) -> dict:
    """Calculate net profit for a Gemini binary arbitrage.

    Buy YES + NO. One always pays $1.00.
    Each leg pays Gemini fee at entry: min(P, 1-P) * fee_rate.
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


def net_profit_gemini_multi(yes_prices: list[float], fee_rate: float = 0.05) -> dict:
    """Calculate net profit for a Gemini categorical (multi-outcome) arbitrage.

    Buy YES on each outcome. Exactly one pays $1.00.
    Each leg pays Gemini fee at entry.
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
    fee_rate: float = 0.05,
) -> dict:
    """Calculate net profit for cross-platform arbitrage: Polymarket vs Gemini.

    poly_side/gm_side: 'yes' or 'no' -- what we're buying on each platform.
    One of the two positions will win $1.00, the other $0.00.
    Gemini charges min(P, 1-P) * fee_rate per contract at entry.
    """
    total_cost = poly_price + gm_price

    if total_cost >= 1.0:
        return {"gross_spread": 1.0 - total_cost, "fees": 0, "net_profit": 1.0 - total_cost}

    gross_spread = 1.0 - total_cost

    # Gemini entry fee (always paid)
    gm_entry_fee = gemini_fee(gm_price, fee_rate)

    # Case 1 (Poly wins): PM 2% winner fee + Gemini entry fee
    case1_fees = polymarket_fee(poly_price, 1.0) + gm_entry_fee
    # Case 2 (Gemini wins): Gemini entry fee only (PM side lost, no fee)
    case2_fees = gm_entry_fee

    fees = _select_fees(case1_fees, case2_fees, poly_price)
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

    # Case 1 (Poly wins): PM 2% winner fee
    case1_fees = polymarket_fee(poly_price, 1.0)
    # Case 2 (IBKR wins): no fees on either side
    case2_fees = 0.0

    fees = _select_fees(case1_fees, case2_fees, poly_price)
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
    """Calculate the winner fee for a platform (fee charged when position pays out $1)."""
    if platform == "polymarket":
        return polymarket_fee(price, 1.0)
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
    if platform == "kalshi":
        return kalshi_taker_fee(price)
    elif platform == "gemini":
        return gemini_fee(price)
    # Other platforms charge on winnings, not at entry
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

    # Case 1 (Poly wins): PM 2% winner fee
    case1_fees = polymarket_fee(poly_price, 1.0)
    # Case 2 (SX Bet wins): no fees on either side
    case2_fees = 0.0

    fees = _select_fees(case1_fees, case2_fees, poly_price)
    gas = POLYGON_GAS_ESTIMATE

    return {
        "gross_spread": gross_spread,
        "fees": fees + gas,
        "net_profit": gross_spread - fees - gas,
    }


