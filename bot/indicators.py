"""Pure technical indicators: RSI, MACD, ATR.

No network, no broker dependency. Only pandas.
"""

from __future__ import annotations

import math

import pandas as pd


def compute_rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    series = pd.Series(prices)
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss_safe = avg_loss.replace(0, float("nan"))
    rs = avg_gain / avg_loss_safe
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.fillna(100.0)  # all gains -> RSI = 100
    val = float(rsi.iloc[-1])
    return val if not math.isnan(val) else 50.0


def compute_macd(prices: list[float], fast: int = 12, slow: int = 26,
                 signal: int = 9) -> dict[str, float]:
    if len(prices) < slow + signal:
        return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}
    series = pd.Series(prices)
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return {
        "macd": float(macd_line.iloc[-1]),
        "signal": float(signal_line.iloc[-1]),
        "histogram": float(histogram.iloc[-1]),
    }


def compute_atr(highs: list[float], lows: list[float], closes: list[float],
                period: int = 14) -> float:
    if len(highs) < period + 1:
        return 0.0
    df = pd.DataFrame({"high": highs, "low": lows, "close": closes})
    df["tr1"] = df["high"] - df["low"]
    df["tr2"] = (df["high"] - df["close"].shift(1)).abs()
    df["tr3"] = (df["low"] - df["close"].shift(1)).abs()
    df["tr"] = df[["tr1", "tr2", "tr3"]].max(axis=1)
    atr = df["tr"].ewm(alpha=1 / period, min_periods=period).mean()
    return float(atr.iloc[-1])