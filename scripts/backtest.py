"""Rules backtest — reuses bot/risk.py on Alpaca daily bars.

Stand-in signal (50/200-day cross) since AI can't be backtested.
Fills at next open with 5 bps slippage. Stops/targets checked on daily high/low.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.data import DataClient
from bot.risk import Proposal, AccountSnapshot, MarketSnapshot, validate


def run_backtest(api_key: str, api_secret: str, symbols: list[str],
                 lookback_days: int = 300) -> dict:
    data = DataClient(api_key, api_secret)
    results: dict[str, dict] = {}

    for symbol in symbols:
        df = data.get_daily_bars(symbol, limit=lookback_days)
        if len(df) < 220:
            continue

        closes = df["close"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        opens_ = df["open"].tolist()

        # 50/200-day trend signal
        sma50 = __import__("pandas").Series(closes).rolling(50).mean()
        sma200 = __import__("pandas").Series(closes).rolling(200).mean()

        equity = 100_000.0
        position = 0
        entry_price = 0.0
        stop_price = 0.0
        target_price = 0.0
        trades = []

        for i in range(200, len(closes)):
            atr = DataClient(api_key, api_secret).get_indicators(symbol) if i == 200 else None
            # simple ATR estimate from recent data
            recent_highs = highs[max(0, i - 14):i + 1]
            recent_lows = lows[max(0, i - 14):i + 1]
            recent_closes = closes[max(0, i - 14):i + 1]
            atr_val = _simple_atr(recent_highs, recent_lows, recent_closes)

            price = closes[i]
            next_open = opens_[i + 1] if i + 1 < len(closes) else price

            # Signal: long when SMA50 > SMA200
            signal_long = sma50.iloc[i] > sma200.iloc[i]

            if position == 0 and signal_long:
                stop_dist = max(1.5 * atr_val, price * 0.02)
                stop_dist = min(stop_dist, price * 0.08)
                stop_price = price - stop_dist
                target_price = price + 1.5 * stop_dist
                risk_shares = int((equity * 0.02) / stop_dist)
                cap_shares = int((equity * 0.15) / price)
                position = min(risk_shares, cap_shares)
                entry_price = next_open * 1.0005  # 5 bps slippage
                if position > 0:
                    trades.append({"day": i, "action": "BUY", "price": entry_price,
                                   "stop": stop_price, "target": target_price,
                                   "shares": position})

            elif position > 0:
                # Check stop/target on daily high/low
                if lows[i] <= stop_price:
                    exit_price = stop_price
                    position = 0
                    trades.append({"day": i, "action": "STOP", "price": exit_price})
                elif highs[i] >= target_price:
                    exit_price = target_price
                    position = 0
                    trades.append({"day": i, "action": "TARGET", "price": exit_price})
                elif not signal_long:
                    exit_price = next_open * 0.9995
                    position = 0
                    trades.append({"day": i, "action": "CLOSE", "price": exit_price})

        results[symbol] = {"trades": trades, "trades_count": len(trades)}

    return results


def _simple_atr(highs: list[float], lows: list[float], closes: list[float]) -> float:
    if len(highs) < 2:
        return 0.0
    trs = []
    for i in range(1, len(highs)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


if __name__ == "__main__":
    import os
    results = run_backtest(
        api_key=os.environ.get("ALPACA_PAPER_KEY", ""),
        api_secret=os.environ.get("ALPACA_PAPER_SECRET", ""),
        symbols=["AAPL", "MSFT", "NVDA", "AMZN", "SPY", "QQQ"],
    )
    for sym, res in results.items():
        print(f"{sym}: {res['trades_count']} trades")