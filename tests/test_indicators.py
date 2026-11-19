"""Tests for technical indicators (RSI, MACD, ATR)."""

import math
import pytest

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


# --- Edge case tests ---

def test_rsi_insufficient_data_raises() -> None:
    """RSI needs period + 1 prices minimum."""
    with pytest.raises(ValueError, match="RSI needs at least"):
        compute_rsi([10.0] * 14)  # period=14 needs 15


def test_rsi_nan_raises() -> None:
    """NaN in prices raises ValueError."""
    prices = [10.0] * 15
    prices[5] = float("nan")
    with pytest.raises(ValueError, match="NaN or infinite"):
        compute_rsi(prices)


def test_rsi_inf_raises() -> None:
    """Infinite value in prices raises ValueError."""
    prices = [10.0] * 15
    prices[5] = float("inf")
    with pytest.raises(ValueError, match="NaN or infinite"):
        compute_rsi(prices)


def test_rsi_zero_loss_returns_100() -> None:
    """When avg_loss == 0 and avg_gain > 0, RSI = 100."""
    prices = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0,
              110.0, 111.0, 112.0, 113.0, 114.0]
    rsi = compute_rsi(prices)
    assert rsi == 100.0


def test_rsi_flat_returns_50() -> None:
    """When no price movement at all, RSI = 50 (no momentum)."""
    prices = [100.0] * 20
    rsi = compute_rsi(prices)
    assert rsi == 50.0


def test_rsi_custom_period() -> None:
    """Custom period works correctly."""
    prices = list(range(30))  # 30 prices
    rsi_7 = compute_rsi(prices, period=7)
    rsi_14 = compute_rsi(prices, period=14)
    rsi_21 = compute_rsi(prices, period=21)
    # All should be > 70 for rising prices
    assert rsi_7 > 70
    assert rsi_14 > 70
    assert rsi_21 > 70


def test_macd_insufficient_data_raises() -> None:
    """MACD needs slow + signal prices minimum."""
    with pytest.raises(ValueError, match="MACD needs at least"):
        compute_macd([10.0] * 34)  # slow=26 + signal=9 = 35


def test_macd_nan_raises() -> None:
    """NaN in prices raises ValueError."""
    prices = [float(i) for i in range(35)]
    prices[10] = float("nan")
    with pytest.raises(ValueError, match="NaN or infinite"):
        compute_macd(prices)


def test_macd_returns_finite_values() -> None:
    """MACD returns finite floats for all components."""
    prices = [float(100 + i * 0.1) for i in range(40)]
    result = compute_macd(prices)
    assert all(math.isfinite(v) for v in result.values())


def test_macd_custom_params() -> None:
    """Custom fast/slow/signal params work."""
    prices = [float(i) for i in range(50)]
    result = compute_macd(prices, fast=8, slow=17, signal=5)
    assert "macd" in result
    assert "signal" in result
    assert "histogram" in result


def test_atr_insufficient_data_raises() -> None:
    """ATR needs period + 1 bars minimum."""
    with pytest.raises(ValueError, match="ATR needs at least"):
        compute_atr([10.0]*14, [9.0]*14, [9.5]*14)  # period=14 needs 15


def test_atr_mismatched_lengths_raises() -> None:
    """Mismatched high/low/close lengths raises ValueError."""
    with pytest.raises(ValueError, match="differ in length"):
        compute_atr([10.0]*15, [9.0]*14, [9.5]*15)


def test_atr_nan_raises() -> None:
    """NaN in any input raises ValueError."""
    highs = [float(10+i) for i in range(20)]
    lows = [float(8+i) for i in range(20)]
    closes = [float(9+i) for i in range(20)]
    highs[5] = float("nan")
    with pytest.raises(ValueError, match="NaN or infinite"):
        compute_atr(highs, lows, closes)


def test_atr_positive_for_all_valid_inputs() -> None:
    """ATR always positive for valid price data."""
    highs = [float(105 + i) for i in range(20)]
    lows = [float(95 + i) for i in range(20)]
    closes = [float(100 + i) for i in range(20)]
    atr = compute_atr(highs, lows, closes)
    assert atr > 0
    assert math.isfinite(atr)


def test_atr_custom_period() -> None:
    """Custom period works."""
    highs = [float(105 + i) for i in range(30)]
    lows = [float(95 + i) for i in range(30)]
    closes = [float(100 + i) for i in range(30)]
    atr_7 = compute_atr(highs, lows, closes, period=7)
    atr_14 = compute_atr(highs, lows, closes, period=14)
    assert atr_7 > 0
    assert atr_14 > 0