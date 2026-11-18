"""Pure technical indicators: RSI, MACD, ATR.

No network, no broker dependency. Only pandas.

Every function raises ValueError when there isn't enough valid data, instead of
returning a neutral-looking placeholder. Callers must handle it.
"""

from __future__ import annotations

import math

import pandas as pd


def _require_finite(name: str, values: list[float]) -> None:
    if not all(math.isfinite(v) for v in values):
        raise ValueError(f"{name} contains NaN or infinite values")


def compute_rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        raise ValueError(f"RSI needs at least {period + 1} prices, got {len(prices)}")
    _require_finite("prices", prices)

    series = pd.Series(prices, dtype="float64")
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    g = float(avg_gain.iloc[-1])
    l = float(avg_loss.iloc[-1])
    if l == 0:
        # No losses at all: RSI is 100, unless price never moved (then 50, no momentum).
        return 50.0 if g == 0 else 100.0
    return 100.0 - 100.0 / (1.0 + g / l)


def compute_macd(prices: list[float], fast: int = 12, slow: int = 26,
                 signal: int = 9) -> dict[str, float]:
    needed = slow + signal
    if len(prices) < needed:
        raise ValueError(f"MACD needs at least {needed} prices, got {len(prices)}")
    _require_finite("prices", prices)

    series = pd.Series(prices, dtype="float64")
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
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError(
            f"ATR inputs differ in length: highs={len(highs)}, lows={len(lows)}, "
            f"closes={len(closes)}"
        )
    if len(highs) < period + 1:
        raise ValueError(f"ATR needs at least {period + 1} bars, got {len(highs)}")
    _require_finite("highs", highs)
    _require_finite("lows", lows)
    _require_finite("closes", closes)

    high = pd.Series(highs, dtype="float64")
    low = pd.Series(lows, dtype="float64")
    close = pd.Series(closes, dtype="float64")
    prev_close = close.shift(1)
    # First row has no previous close; max(axis=1) skips the NaNs, leaving high - low.
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return float(atr.iloc[-1])