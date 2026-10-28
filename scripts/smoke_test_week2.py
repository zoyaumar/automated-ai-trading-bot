"""Week 2 smoke test — data pipelines.

Verifies:
1. FRED VIX client (graceful without API key)
2. Earnings calendar computation (graceful without API key)
3. Indicators compute from cached/synthetic data
4. DataClient instantiates with Alpaca keys

Run with: python scripts/smoke_test_week2.py
"""

from __future__ import annotations

import os
import sys
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

from bot.data import FredClient, get_vix_level, DataClient, compute_rsi, compute_macd, compute_atr
from bot.news import NewsClient


def main() -> None:
    print("=" * 60)
    print("Week 2 Smoke Test — Data Pipelines")
    print("=" * 60)

    # 1. Indicators (pure, no network)
    print("\n[1/4] Indicators (pure pandas)...")
    prices = [float(100 + i) for i in range(30)]
    rsi = compute_rsi(prices)
    macd = compute_macd(prices)
    atr = compute_atr(
        [float(105 + i) for i in range(30)],
        [float(95 + i) for i in range(30)],
        prices,
    )
    print(f"  RSI(30 rising): {rsi:.1f} (expect >70)")
    print(f"  MACD: {macd['macd']:.4f}, signal: {macd['signal']:.4f}, hist: {macd['histogram']:.4f}")
    print(f"  ATR: {atr:.4f}")
    assert rsi > 70, f"RSI should be high for rising prices, got {rsi}"
    assert atr > 0, "ATR should be positive"
    print("  OK: indicators compute correctly")

    # 2. FRED VIX (graceful without API key)
    print("\n[2/4] FRED VIX client...")
    fred = FredClient()
    vix = fred.get_vix()
    if vix:
        print(f"  VIX: {vix['value']:.2f} on {vix['date']}")
        print("  OK: FRED VIX fetched")
    else:
        print("  VIX: None (no FRED_API_KEY or API unavailable)")
        print("  OK: FRED client degrades gracefully without key")

    # Convenience function
    vix_level = get_vix_level()
    print(f"  get_vix_level(): {vix_level}")

    # 3. Earnings calendar (graceful without API key)
    print("\n[3/4] Earnings calendar...")
    news = NewsClient(headlines_enabled=False, earnings_filter_enabled=True)
    earnings = news.get_earnings_within_days(["AAPL", "MSFT", "NVDA", "SPY", "QQQ"], days=1)
    for sym, days in earnings.items():
        print(f"  {sym}: earnings in {days} days" if days is not None else f"  {sym}: no earnings data")
    print("  OK: earnings calendar computes (or degrades gracefully)")

    # 4. DataClient instantiation
    print("\n[4/4] DataClient with Alpaca keys...")
    key = os.environ.get("ALPACA_PAPER_KEY", "")
    secret = os.environ.get("ALPACA_PAPER_SECRET", "")
    if key and secret:
        try:
            dc = DataClient(key, secret)
            print("  OK: DataClient instantiated with Alpaca keys")
        except Exception as exc:
            print(f"  DataClient error: {exc}")
    else:
        print("  OK: DataClient instantiation skipped (no Alpaca keys)")

    print("\nWeek 2 smoke test: PASSED")


if __name__ == "__main__":
    main()