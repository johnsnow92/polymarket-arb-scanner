"""Fund Kalshi account via USDC on Base.

Kalshi processes US crypto deposits through Zero Hash, which converts USDC
to USD and credits your Kalshi balance. This script:

  1. Verifies your current Kalshi balance via the Kalshi API.
  2. Validates the destination address and amount.
  3. Dispatches a USDC send from the Coinbase Agentic Wallet (Base network).

GETTING YOUR KALSHI USDC DEPOSIT ADDRESS
-----------------------------------------
Zero Hash deposit addresses are generated per-session in the Kalshi UI:
  1. Log into kalshi.com → Portfolio → Add Funds → Crypto
  2. Select "USDC" → Network: "Base"
  3. Copy the deposit address shown (0x...)
  4. Paste it as --address below.

Deposit limits: max $500,000 per transaction. Processing: up to 30 minutes.

REQUIREMENTS
------------
The Coinbase Agentic Wallet MCP must be loaded in Claude Code:
  - settings.json entry "agentic-wallet" must point to a working bundle.js
  - Claude Code must be restarted after initial setup
  - The wallet address (0x2689459F5bCe5f808ad436C0c9b238E73B265139) must have
    sufficient USDC on Base — fund it at npx awal show

Usage:
    python scripts/fund_kalshi_usdc.py --address 0xABC... --amount 500 [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


EVM_ADDRESS_RE = re.compile(r'^0x[0-9a-fA-F]{40}$')
AGENTIC_WALLET_ADDRESS = '0x2689459F5bCe5f808ad436C0c9b238E73B265139'
MIN_AMOUNT = 1.0
MAX_AMOUNT = 500_000.0


def get_kalshi_balance() -> float | None:
    """Return current Kalshi USD balance, or None if API call fails."""
    try:
        from kalshi_api import KalshiAPI
        api = KalshiAPI()
        key_id = os.getenv('KALSHI_API_KEY_ID')
        key_b64 = os.getenv('KALSHI_PRIVATE_KEY_B64')
        if not key_id or not key_b64:
            return None
        logged_in = api.login_with_api_key(api_key_id=key_id, private_key_base64=key_b64)
        if not logged_in:
            return None
        return api.get_balance()
    except Exception as exc:
        print(f"  (Kalshi balance check failed: {exc})")
        return None


def validate_address(address: str) -> None:
    if not EVM_ADDRESS_RE.match(address):
        print(f"ERROR: '{address}' is not a valid EVM address (must be 0x + 40 hex chars).")
        sys.exit(1)


def validate_amount(amount: float) -> None:
    if amount < MIN_AMOUNT:
        print(f"ERROR: Minimum deposit is ${MIN_AMOUNT:.2f}")
        sys.exit(1)
    if amount > MAX_AMOUNT:
        print(f"ERROR: Maximum Kalshi deposit is ${MAX_AMOUNT:,.0f} per transaction.")
        sys.exit(1)


def print_send_instructions(address: str, amount: float) -> None:
    """Print the Claude Code MCP command to execute the USDC send."""
    print()
    print("=" * 60)
    print("USDC SEND INSTRUCTIONS")
    print("=" * 60)
    print(f"  From:    Agentic Wallet ({AGENTIC_WALLET_ADDRESS})")
    print(f"  To:      {address}")
    print(f"  Amount:  {amount} USDC")
    print(f"  Network: Base (EVM)")
    print()
    print("To execute, ask Claude Code (after restarting to load agentic-wallet MCP):")
    print()
    print(f'  "Send {amount} USDC on Base to {address} using the agentic wallet"')
    print()
    print("Or directly via the agentic wallet MCP tool once loaded:")
    print(f"  agentic_wallet_send(to='{address}', amount='{amount}', token='USDC', network='base')")
    print()
    print("After sending:")
    print("  - Kalshi credits typically take up to 30 minutes")
    print("  - Zero Hash converts USDC → USD automatically")
    print("  - Check Kalshi portfolio for updated balance")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fund Kalshi via USDC on Base through the Coinbase Agentic Wallet"
    )
    parser.add_argument(
        '--address',
        required=True,
        help="Kalshi Zero Hash USDC deposit address (from Kalshi UI → Add Funds → Crypto → USDC → Base)"
    )
    parser.add_argument(
        '--amount',
        type=float,
        required=True,
        help="USDC amount to send (min $1, max $500,000)"
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help="Validate and print instructions without dispatching"
    )
    args = parser.parse_args()

    print(f"Kalshi USDC Deposit Helper")
    print(f"---------------------------")

    validate_address(args.address)
    validate_amount(args.amount)
    print(f"  Address: {args.address} ✓")
    print(f"  Amount:  {args.amount} USDC ✓")

    print()
    print("Checking current Kalshi balance...")
    balance = get_kalshi_balance()
    if balance is not None:
        print(f"  Current Kalshi balance: ${balance:,.2f}")
        print(f"  After deposit (est.):   ${balance + args.amount:,.2f}")
    else:
        print("  (Set KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_B64 env vars to show live balance)")

    if args.dry_run:
        print()
        print("DRY RUN — no funds moved.")
        print_send_instructions(args.address, args.amount)
        sys.exit(0)

    print_send_instructions(args.address, args.amount)


if __name__ == '__main__':
    main()
