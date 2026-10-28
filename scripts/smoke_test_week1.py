"""Week 1 end-to-end smoke test (dry-run, no orders placed).

Verifies the full Week 1 stack works together against real Alpaca paper data:
1. config.yaml loads and validates
2. .env secrets load
3. Broker connects to Alpaca paper and returns account/positions/clock
4. Risk validator runs against real account data (a fake BUY proposal)
5. SQLite store logs cycle, prompt, decision, and equity snapshot
6. No order is actually submitted (dry-run)

Run with: python scripts/smoke_test_week1.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- Load .env ---
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

from bot.config import Settings
from bot.broker import Broker
from bot.risk import (
    Proposal, AccountSnapshot, MarketSnapshot, validate,
)
from bot.store import Store


def main() -> None:
    print("=" * 60)
    print("Week 1 End-to-End Smoke Test (dry-run)")
    print("=" * 60)

    # 1. Config
    print("\n[1/5] Loading config.yaml...")
    settings = Settings("config.yaml")
    cfg = settings.as_dict
    print(f"  mode={cfg['mode']}")
    print(f"  watchlist={cfg['watchlist']}")
    print(f"  config_hash={settings.config_hash}")
    print("  OK: config loaded and validated")

    # 2. Broker
    print("\n[2/5] Connecting to Alpaca paper trading...")
    key = os.environ.get("ALPACA_PAPER_KEY", "")
    secret = os.environ.get("ALPACA_PAPER_SECRET", "")
    if not key or not secret:
        print("  ERROR: Alpaca paper keys not found in .env")
        sys.exit(1)
    broker = Broker(key, secret, paper=True)
    acct = broker.get_account()
    clock = broker.get_clock()
    print(f"  equity=${acct.equity:,.2f} cash=${acct.cash:,.2f} buying_power=${acct.buying_power:,.2f}")
    print(f"  market_open={clock.is_open} positions={len(acct.positions)} open_orders={len(acct.open_orders)}")
    print("  OK: broker connected")

    # 3. Risk validator (dry-run against real account data)
    print("\n[3/5] Running risk validator against real account data...")
    # Build a fake BUY proposal for AAPL
    proposal = Proposal(
        action="BUY",
        ticker="AAPL",
        confidence=0.8,
        stop_loss_pct=5.0,
        take_profit_pct=10.0,
        reasoning="smoke test proposal",
    )
    market = MarketSnapshot(
        price=200.0,  # hypothetical price
        atr=5.0,
        quote_age_seconds=5.0,
        websocket_healthy=True,
        earnings_within_days=None,
        market_open=clock.is_open,
        minutes_since_open=60,
        minutes_to_close=300,
    )
    decision = validate(proposal, acct, market, cfg)
    print(f"  action={proposal.action} ticker={proposal.ticker}")
    print(f"  approved={decision.approved} shares={decision.shares} entry={decision.entry} stop={decision.stop} target={decision.target}")
    print(f"  reasons={decision.reasons}")
    if decision.approved:
        print("  OK: proposal passed risk firewall (dry-run)")
    else:
        print(f"  OK: proposal rejected by risk firewall ({decision.reasons})")

    # 4. SQLite store
    print("\n[4/5] Writing to SQLite audit log...")
    store = Store("data/bot.db")
    cycle_id = store.insert_cycle(
        mode=cfg["mode"],
        config_hash=settings.config_hash,
        market_open=clock.is_open,
        paused=False,
        drawdown_suspended=False,
    )
    store.insert_decision(
        cycle_id=cycle_id,
        ticker=proposal.ticker,
        approved=decision.approved,
        shares=decision.shares,
        entry_price=decision.entry,
        stop_price=decision.stop,
        target_price=decision.target,
        reason="; ".join(decision.reasons or []),
        config_hash=settings.config_hash,
    )
    store.insert_equity_snapshot(
        equity=acct.equity,
        cash=acct.cash,
        buying_power=acct.buying_power,
        daily_start_equity=acct.equity,
        high_water_mark=acct.equity,
        drawdown_pct=0.0,
    )
    store.insert_alert("smoke_test", "Week 1 end-to-end smoke test completed")
    store.close()
    print(f"  OK: cycle {cycle_id} logged to data/bot.db")

    # 5. Summary
    print("\n[5/5] Week 1 smoke test: PASSED")
    print("  Config: valid")
    print("  Broker: connected to Alpaca paper")
    print("  Risk: validator executed against real account data")
    print("  Store: cycle, decision, equity snapshot, alert logged")
    print("\nNo orders were placed. This was a dry-run.")


if __name__ == "__main__":
    main()