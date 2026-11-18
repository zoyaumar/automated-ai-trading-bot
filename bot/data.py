"""Market data: WebSocket stream, daily bars, indicators (RSI/MACD/ATR), FRED VIX."""

from __future__ import annotations

import logging
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from .indicators import compute_rsi, compute_macd, compute_atr

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Daily bars are treated as final once the session is over (with a small buffer).
BARS_FINAL_AFTER = dtime(16, 15)

# Judgment call: below this, EMA-based indicators haven't converged and won't match
# charting platforms. Lower it if you want to trade recent listings.
MIN_INDICATOR_BARS = 100

# Free-plan websocket symbol cap as far as I know; verify against your plan.
WS_SYMBOL_LIMIT = 30


def _is_finite(v: Any) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


class DataClient:
    """Fetches daily bars and computes indicators. WebSocket managed separately."""

    def __init__(
        self, api_key: str, api_secret: str, feed: DataFeed = DataFeed.IEX
    ) -> None:
        self._data = StockHistoricalDataClient(api_key, api_secret)
        self._feed = feed
        # (symbol, limit) -> (stamp, completed daily bars)
        self._bars_cache: dict[tuple[str, int], tuple[tuple[date, bool], pd.DataFrame]] = {}

    @staticmethod
    def _cache_stamp() -> tuple[date, bool]:
        """Cache is valid for one ET date, and refreshed once more after the close."""
        now = datetime.now(ET)
        return now.date(), now.time() >= BARS_FINAL_AFTER

    def _fetch_bars(
        self, symbols: list[str], limit: int, stamp: tuple[date, bool]
    ) -> dict[str, pd.DataFrame]:
        """One request for any number of symbols. Returns completed daily bars only."""
        today, session_final = stamp
        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            # Explicit start: without it Alpaca defaults to the start of today.
            start=datetime.now(timezone.utc) - timedelta(days=int(limit * 1.6) + 10),
            adjustment=Adjustment.SPLIT,  # splits only; dividend-adjusted closes
            feed=self._feed,              # wouldn't match the prices you trade
        )
        raw = self._data.get_stock_bars(req).df
        if raw.empty:
            return {}

        out: dict[str, pd.DataFrame] = {}
        for sym, sub in raw.groupby(level="symbol"):
            df = sub.droplevel("symbol")
            # Drop today's in-progress bar so indicators only see completed days.
            if (
                not session_final
                and len(df)
                and df.index[-1].astimezone(ET).date() == today
            ):
                df = df.iloc[:-1]
            df = df.tail(limit)
            if not df.empty:
                out[str(sym)] = df
        return out

    def get_daily_bars(self, symbol: str, limit: int = 300) -> pd.DataFrame:
        sym = symbol.upper()
        stamp = self._cache_stamp()
        entry = self._bars_cache.get((sym, limit))
        if entry is not None and entry[0] == stamp:
            return entry[1].copy()

        bars = self._fetch_bars([sym], limit, stamp)
        df = bars.get(sym)
        if df is None:
            raise ValueError(f"No daily bars returned for {sym}")
        self._bars_cache[(sym, limit)] = (stamp, df)
        return df.copy()

    def prefetch_daily_bars(self, symbols: list[str], limit: int = 300) -> list[str]:
        """Warm the cache for many symbols with a single request.

        Returns the symbols that came back with no data.
        """
        syms = sorted({s.upper() for s in symbols})
        stamp = self._cache_stamp()
        needed = []
        for s in syms:
            entry = self._bars_cache.get((s, limit))
            if entry is None or entry[0] != stamp:
                needed.append(s)
        if not needed:
            return []

        bars = self._fetch_bars(needed, limit, stamp)
        for s, df in bars.items():
            self._bars_cache[(s, limit)] = (stamp, df)
        missing = sorted(set(needed) - set(bars))
        if missing:
            logger.warning("No daily bars returned for: %s", ", ".join(missing))
        return missing

    def get_indicators(self, symbol: str) -> dict[str, float]:
        """Indicators computed on completed daily bars.

        Raises ValueError if there is no data, too few bars, or any indicator is
        invalid, so callers never receive a placeholder value or a fake price.
        `last_close` is the last completed daily close, not a live price.
        """
        sym = symbol.upper()
        df = self.get_daily_bars(sym)
        if len(df) < MIN_INDICATOR_BARS:
            raise ValueError(
                f"{sym}: only {len(df)} completed bars, need {MIN_INDICATOR_BARS}"
            )

        closes = df["close"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()

        try:
            macd = compute_macd(closes)
            values = {
                "rsi": compute_rsi(closes),
                "macd": macd["macd"],
                "macd_signal": macd["signal"],
                "macd_histogram": macd["histogram"],
                "atr": compute_atr(highs, lows, closes),
                "last_close": closes[-1],
            }
        except ValueError as exc:
            raise ValueError(f"{sym}: {exc}") from exc

        bad = [k for k, v in values.items() if not _is_finite(v)]
        if bad:
            raise ValueError(f"{sym}: cannot compute {', '.join(bad)}")
        if values["atr"] <= 0:
            raise ValueError(f"{sym}: ATR is zero (flat or halted); can't size a stop")
        return {k: float(v) for k, v in values.items()}


class WebSocketStream:
    """Alpaca WebSocket stream for last-quote tracking per symbol.

    Note: IEX quotes are IEX's own book, not the NBBO. Spreads can look wide or
    stale on thin names.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbols: list[str],
        feed: DataFeed = DataFeed.IEX,
    ) -> None:
        self._stream = StockDataStream(api_key, api_secret, feed=feed)
        self._symbols = sorted({s.upper() for s in symbols})
        self._symbol_set = set(self._symbols)
        self._last_quote: dict[str, dict[str, Any]] = {}
        self._last_quote_time: dict[str, datetime] = {}
        self._subscribed = False
        if len(self._symbols) > WS_SYMBOL_LIMIT:
            logger.warning(
                "Subscribing to %d symbols; the stream may reject more than %d on some plans",
                len(self._symbols),
                WS_SYMBOL_LIMIT,
            )

    async def _on_quote(self, data: Any) -> None:
        symbol = data.symbol
        if symbol not in self._symbol_set:
            return
        bid, ask = data.bid_price, data.ask_price
        # Reject empty/zero/crossed quotes so consumers computing a mid don't
        # crash or get nonsense. Rejected quotes don't count as fresh.
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
            return
        self._last_quote[symbol] = {
            "bid_price": float(bid),
            "ask_price": float(ask),
            "bid_size": data.bid_size,
            "ask_size": data.ask_size,
        }
        self._last_quote_time[symbol] = datetime.now(timezone.utc)

    def run(self) -> None:
        """Subscribe and run the stream (blocking)."""
        if not self._symbols:
            raise ValueError("No symbols to subscribe to")
        if not self._subscribed:
            self._stream.subscribe_quotes(self._on_quote, *self._symbols)
            self._subscribed = True
        self._stream.run()

    def is_healthy(self, max_age: float = 60.0) -> bool:
        """True if any quote arrived within max_age seconds.

        There are no quotes outside market hours, so only call this when the
        clock says the market is open.
        """
        times = tuple(self._last_quote_time.values())  # snapshot: stream thread writes
        if not times:
            return False
        return (datetime.now(timezone.utc) - max(times)).total_seconds() < max_age

    def get_quote(self, symbol: str) -> dict[str, Any] | None:
        return self._last_quote.get(symbol.upper())

    def quote_age_seconds(self, symbol: str) -> float:
        t = self._last_quote_time.get(symbol.upper())
        if t is None:
            return float("inf")
        return (datetime.now(timezone.utc) - t).total_seconds()


class VixObservation(TypedDict):
    value: float
    date: str


class FredClient:
    """FRED macro data client — VIX series for market context.

    FRED API is free (120 requests/minute). VIX (series id: VIXCLS) is updated
    daily with a lag, so results are cached for a few hours.
    """

    def __init__(
        self,
        api_key: str | None = None,
        cache_ttl_seconds: float = 3 * 3600,
        max_age_days: int = 5,
    ) -> None:
        self.api_key = api_key or os.environ.get("FRED_API_KEY", "")
        self.base_url = "https://api.stlouisfed.org/fred/series/observations"
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_age_days = max_age_days
        self._cache: tuple[float, VixObservation] | None = None  # (monotonic time, obs)

    def _fetch_vix(self) -> VixObservation | None:
        params = {
            "series_id": "VIXCLS",
            "api_key": self.api_key,
            "file_type": "json",
            "limit": 10,  # latest rows can be "." (holiday / not yet posted)
            "sort_order": "desc",
        }
        # Don't log exception messages here: requests errors often include the
        # full URL, which contains the api_key.
        try:
            resp = requests.get(self.base_url, params=params, timeout=10)
            resp.raise_for_status()
            observations = resp.json().get("observations", [])
            for obs in observations:
                v = obs.get("value")
                if v not in (".", "", None):
                    return {"value": float(v), "date": obs["date"]}
            logger.error("FRED VIX response had no valid observations")
            return None
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            logger.error("FRED VIX request failed: HTTP %s", status)
        except requests.RequestException as exc:
            logger.error("FRED VIX request failed: %s", type(exc).__name__)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.error("FRED VIX response could not be parsed: %s", type(exc).__name__)
        return None

    def _is_recent_enough(self, obs: VixObservation) -> bool:
        try:
            obs_date = date.fromisoformat(obs["date"])
        except (KeyError, ValueError):
            return False
        age_days = (datetime.now(timezone.utc).date() - obs_date).days
        return age_days <= self.max_age_days

    def get_vix(self) -> VixObservation | None:
        """Return the latest valid VIX observation as {value, date}, or None.

        None means "no usable data" (missing key, outage, or an observation older
        than max_age_days). Callers should decide explicitly what that means for
        trading; don't treat it as "volatility is normal".
        """
        if not self.api_key:
            logger.warning("FRED_API_KEY not set; VIX unavailable")
            return None

        now = time.monotonic()
        cached = self._cache
        if cached is not None and now - cached[0] < self.cache_ttl_seconds:
            obs: VixObservation | None = cached[1]
        else:
            obs = self._fetch_vix()
            if obs is not None:
                self._cache = (now, obs)
            elif cached is not None:
                obs = cached[1]  # fetch failed; fall back to last good value
                logger.warning("Using cached VIX from %s after fetch failure", obs["date"])

        if obs is None:
            return None
        if not self._is_recent_enough(obs):
            logger.warning(
                "VIX observation dated %s is older than %d days; ignoring",
                obs["date"],
                self.max_age_days,
            )
            return None
        return obs


_fred_clients: dict[str, FredClient] = {}


def get_vix_level(api_key: str | None = None) -> float | None:
    """Convenience: return latest VIX level or None. Reuses one cached client per key."""
    key = api_key or os.environ.get("FRED_API_KEY", "")
    client = _fred_clients.get(key)
    if client is None:
        client = _fred_clients[key] = FredClient(key)
    result = client.get_vix()
    return result["value"] if result else None