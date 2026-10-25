"""Tests for technical indicators (RSI, MACD, ATR)."""

from bot.indicators import compute_rsi, compute_macd, compute_atr


def test_rsi_known_values() -> None:
    # Rising prices -> RSI near 100
    prices = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24]
    rsi = compute_rsi(prices)
    assert rsi > 70

    # Falling prices -> RSI near 0
    prices = [24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10]
    rsi = compute_rsi(prices)
    assert rsi < 30


def test_macd_structure() -> None:
    prices = [float(i) for i in range(40)]
    result = compute_macd(prices)
    assert "macd" in result
    assert "signal" in result
    assert "histogram" in result


def test_atr_positive() -> None:
    highs = [float(10 + i) for i in range(20)]
    lows = [float(8 + i) for i in range(20)]
    closes = [float(9 + i) for i in range(20)]
    atr = compute_atr(highs, lows, closes)
    assert atr > 0