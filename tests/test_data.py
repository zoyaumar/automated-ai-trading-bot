"""Tests for DataClient, FRED VIX, and market data."""

import math
import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from bot.data import DataClient, FredClient, get_vix_level, compute_rsi, compute_macd, compute_atr


# --- DataClient tests ---

def test_data_client_cache_stamp() -> None:
    """Cache stamp returns (today_et, session_final)."""
    dc = DataClient("key", "secret")
    stamp = dc._cache_stamp()
    assert isinstance(stamp[0], date)
    assert isinstance(stamp[1], bool)


def test_data_client_bars_final_after_close() -> None:
    """In-progress bar dropped before 16:15 ET."""
    from bot.data import ET
    dc = DataClient("key", "secret")

    # Create mock bars with today's date as last bar (in ET)
    today_et = datetime.now(ET).date()
    yesterday_et = today_et - timedelta(days=1)
    df = pd.DataFrame({
        "open": [100.0, 101.0],
        "high": [102.0, 103.0],
        "low": [99.0, 100.0],
        "close": [101.0, 102.0],
        "volume": [1000, 2000],
    }, index=pd.MultiIndex.from_tuples([
        ("AAPL", pd.Timestamp(yesterday_et, tz=ET)),
        ("AAPL", pd.Timestamp(today_et, tz=ET)),
    ], names=["symbol", "timestamp"]))

    mock_bars = MagicMock()
    mock_bars.df = df
    with patch.object(dc._data, "get_stock_bars", return_value=mock_bars):
        # Before 16:15 ET - today's bar should be dropped
        stamp = (today_et, False)
        result = dc._fetch_bars(["AAPL"], 2, stamp)
        assert len(result["AAPL"]) == 1  # only yesterday

        # After 16:15 ET - today's bar kept
        stamp = (today_et, True)
        result = dc._fetch_bars(["AAPL"], 2, stamp)
        assert len(result["AAPL"]) == 2


def test_data_client_get_indicators_requires_min_bars() -> None:
    """get_indicators raises if insufficient bars."""
    dc = DataClient("key", "secret")
    # Mock empty bars (less than 100)
    with patch.object(dc, "get_daily_bars", return_value=pd.DataFrame()):
        with pytest.raises(ValueError, match="completed bars, need 100"):
            dc.get_indicators("AAPL")


def test_data_client_get_indicators_computes_correctly() -> None:
    """get_indicators returns expected indicator values."""
    dc = DataClient("key", "secret")
    # Create 120 bars of rising prices
    dates = pd.date_range(end=datetime.now(timezone.utc).date(), periods=120, freq="D", tz=timezone.utc)
    df = pd.DataFrame({
        "open": [float(100 + i) for i in range(120)],
        "high": [float(102 + i) for i in range(120)],
        "low": [float(98 + i) for i in range(120)],
        "close": [float(101 + i) for i in range(120)],
        "volume": [1000] * 120,
    }, index=pd.MultiIndex.from_tuples([("AAPL", d) for d in dates], names=["symbol", "timestamp"]))

    with patch.object(dc, "get_daily_bars", return_value=df.xs("AAPL", level="symbol")):
        result = dc.get_indicators("AAPL")
        assert "rsi" in result
        assert "macd" in result
        assert "atr" in result
        assert math.isfinite(result["rsi"])
        assert math.isfinite(result["macd"])
        assert math.isfinite(result["atr"])


# --- FredClient tests ---

def test_fred_client_no_key_returns_none(monkeypatch) -> None:
    """FredClient returns None when no API key."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    client = FredClient("")
    assert client.get_vix() is None


def test_fred_client_cache_ttl() -> None:
    """FredClient caches results for cache_ttl_seconds."""
    client = FredClient("fake_key")
    assert client.cache_ttl_seconds == 10800  # 3 hours default


def test_fred_client_max_age_days() -> None:
    """FredClient ignores observations older than max_age_days."""
    client = FredClient("fake_key", max_age_days=1)
    obs = {"value": 20.0, "date": (date.today() - timedelta(days=2)).isoformat()}
    assert client._is_recent_enough(obs) is False


def test_get_vix_level_reuses_client() -> None:
    """get_vix_level reuses cached FredClient per key."""
    with patch("bot.data.FredClient.get_vix", return_value={"value": 22.5, "date": "2024-01-01"}):
        v1 = get_vix_level("test_key")
        v2 = get_vix_level("test_key")
        assert v1 == v2 == 22.5


# --- Indicator integration tests ---

def test_compute_rsi_via_data_module() -> None:
    """compute_rsi accessible from data module."""
    prices = [float(100 + i) for i in range(20)]
    rsi = compute_rsi(prices)
    assert 0 <= rsi <= 100


def test_compute_macd_via_data_module() -> None:
    """compute_macd accessible from data module."""
    prices = [float(i) for i in range(40)]
    result = compute_macd(prices)
    assert "macd" in result
    assert "signal" in result
    assert "histogram" in result


def test_compute_atr_via_data_module() -> None:
    """compute_atr accessible from data module."""
    highs = [float(105 + i) for i in range(20)]
    lows = [float(95 + i) for i in range(20)]
    closes = [float(100 + i) for i in range(20)]
    atr = compute_atr(highs, lows, closes)
    assert atr > 0


# --- Edge cases ---

def test_data_client_ws_symbol_limit() -> None:
    """WS_SYMBOL_LIMIT constant exists."""
    from bot.data import WS_SYMBOL_LIMIT
    assert WS_SYMBOL_LIMIT == 30


def test_fred_client_invalid_json_handled() -> None:
    """FredClient handles invalid JSON gracefully."""
    client = FredClient("fake_key")
    with patch("requests.get", side_effect=ValueError("bad json")):
        assert client._fetch_vix() is None


def test_fred_client_http_error_handled() -> None:
    """FredClient handles HTTP errors gracefully."""
    import requests
    client = FredClient("fake_key")
    with patch("requests.get", side_effect=requests.HTTPError("404")):
        assert client._fetch_vix() is None