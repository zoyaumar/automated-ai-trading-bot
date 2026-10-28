"""Alpaca paper-trading connectivity test.

Reads keys from .env and verifies:
1. Alpaca paper API keys are valid
2. Account object fields (equity, cash, buying_power, etc.)
3. Market clock state
4. Positions and open orders are readable

Run with: python scripts/test_alpaca_connection.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Load .env manually (no external dep needed for this test)
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if key and val and key not in os.environ:
            os.environ[key] = val

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alpaca.trading.client import TradingClient


def main() -> None:
    key = os.environ.get("ALPACA_PAPER_KEY", "")
    secret = os.environ.get("ALPACA_PAPER_SECRET", "")

    if not key or not secret:
        print("ERROR: ALPACA_PAPER_KEY and ALPACA_PAPER_SECRET not found in .env")
        sys.exit(1)

    print(f"Connecting to Alpaca Paper Trading with key {key[:8]}...")

    try:
        client = TradingClient(key, secret, paper=True)

        # 1. Account
        acct = client.get_account()
        print("\n--- Account ---")
        print(f"  Account ID:       {acct.id}")
        print(f"  Status:           {acct.status}")
        print(f"  Equity:           ${float(acct.equity):,.2f}")
        print(f"  Cash:             ${float(acct.cash):,.2f}")
        print(f"  Buying Power:     ${float(acct.buying_power):,.2f}")
        print(f"  Multiplier:       {acct.multiplier}")
        print(f"  Day Trading Count:{acct.daytrade_count}")
        print(f"  Pattern Day Trader:{acct.pattern_day_trader}")

        # 2. Clock
        clock = client.get_clock()
        print("\n--- Market Clock ---")
        print(f"  Market Open:      {clock.is_open}")
        print(f"  Next Open:        {clock.next_open}")
        print(f"  Next Close:       {clock.next_close}")

        # 3. Positions
        positions = client.get_all_positions()
        print(f"\n--- Positions ({len(positions)}) ---")
        for p in positions:
            print(f"  {p.symbol}: {p.qty} shares @ ${float(p.avg_entry_price):.2f} "
                  f"(market value ${float(p.market_value):,.2f}, "
                  f"unrealized P&L ${float(p.unrealized_pl):,.2f})")
        if not positions:
            print("  (none)")

        # 4. Open orders
        orders = client.get_orders()
        open_orders = [o for o in orders if o.status.value == "open" if hasattr(o, 'status')]
        print(f"\n--- Orders ({len(orders)} total) ---")
        for o in orders:
            status = getattr(o, 'status', 'unknown')
            side = getattr(o, 'side', 'unknown')
            qty = getattr(o, 'qty', 'unknown')
            limit_price = getattr(o, 'limit_price', None)
            print(f"  {o.symbol} {side} {qty} @ "
                  f"{float(limit_price) if limit_price else 'market'} ({status})")
        if not orders:
            print("  (none)")

        print("\nAlpaca paper connection: OK")

    except Exception as exc:
        print(f"\nAlpaca paper connection: FAILED")
        print(f"  Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()