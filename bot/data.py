"""Market data: WebSocket stream, daily bars, indicators (RSI/MACD/ATR), FRED VIX."""

from __future__ import annotations

import math
import os
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd
import requests

from alpaca.data.live import StockDataStream
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from .indicators import compute_rsi, compute_macd, compute_atr


class DataClient:
    """Fetches daily bars and computes indicators. WebSocket managed separately."""

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._data = StockHistoricalDataClient(api_key, api_secret)
        self._bars_cache: dict[str, pd.DataFrame] = {}

    def get_daily_bars(self, symbol: str, limit: int = 300) -> pd.DataFrame:
        if symbol in self._bars_cache:
            return self._bars_cache[symbol]
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            limit=limit,
        )
        df = self._data.get_stock_bars(req).df
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level="symbol")
        self._bars_cache[symbol] = df
        return df

    def get_indicators(self, symbol: str) -> dict[str, float]:
        df = self.get_daily_bars(symbol)
        closes = df["close"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        return {
            "rsi": compute_rsi(closes),
            "macd": compute_macd(closes)["macd"],
            "macd_signal": compute_macd(closes)["signal"],
            "macd_histogram": compute_macd(closes)["histogram"],
            "atr": compute_atr(highs, lows, closes),
            "price": float(closes[-1]) if closes else 0.0,
        }


class WebSocketStream:
    """Alpaca WebSocket stream for last-quote tracking per symbol."""

    def __init__(self, api_key: str, api_secret: str, symbols: list[str],
                 feed: str = "iex") -> None:
        self._stream = StockDataStream(api_key, api_secret, feed=feed)
        self._symbols = symbols
        self._last_quote: dict[str, dict[str, Any]] = {}
        self._last_quote_time: dict[str, datetime] = {}
        self._healthy = False

    async def _on_quote(self, data: Any) -> None:
        symbol = getattr(data, "symbol", None) or getattr(data, "S", None)
        if symbol not in self._symbols:
            return
        self._last_quote[symbol] = {
            "bid_price": getattr(data, "bid_price", None) or getattr(data, "bp", None),
            "ask_price": getattr(data, "ask_price", None) or getattr(data, "ap", None),
            "bid_size": getattr(data, "bid_size", None) or getattr(data, "bs", None),
            "ask_size": getattr(data, "ask_size", None) or getattr(data, "as", None),
        }
        self._last_quote_time[symbol] = datetime.now(timezone.utc)
        self._healthy = True

    async def _on_status(self, data: Any) -> None:
        status = getattr(data, "status", None)
        if status == "connected":
            self._healthy = True
        elif status == "disconnected":
            self._healthy = False

    def run(self) -> None:
        """Subscribe and run the stream (blocking)."""
        for symbol in self._symbols:
            self._stream.subscribe_quotes(self._on_quote, symbol)
        self._stream.run()

    def is_healthy(self) -> bool:
        return self._healthy

    def get_quote(self, symbol: str) -> dict[str, Any] | None:
        return self._last_quote.get(symbol)

    def quote_age_seconds(self, symbol: str) -> float:
        t = self._last_quote_time.get(symbol)
        if t is None:
            return float("inf")
        return (datetime.now(timezone.utc) - t).total_seconds()


class FredClient:
    """FRED macro data client — VIX series for market context.

    FRED API is free. VIX (series id: VIXCLS) is updated daily.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("FRED_API_KEY", "")
        self.base_url = "https://api.stlouisfed.org/fred/series/observations"

    def get_vix(self) -> dict[str, object] | None:
        """Fetch the latest VIX observation.

        Returns {value, date} or None on failure.
        """
        if not self.api_key:
            return None
        try:
            params = {
                "series_id": "VIXCLS",
                "api_key": self.api_key,
                "file_type": "json",
                "limit": 1,
                "sort_order": "desc",
            }
            resp = requests.get(self.base_url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            obs = data.get("observations", [])
            if not obs:
                return None
            latest = obs[0]
            value = latest.get("value")
            if value in (".", "", None):
                return None
            return {
                "value": float(value),
                "date": latest.get("date"),
            }
        except Exception:
            return None


def get_vix_level(api_key: str | None = None) -> float | None:
    """Convenience: return latest VIX level or None."""
    client = FredClient(api_key)
    result = client.get_vix()
    return result["value"] if result else None