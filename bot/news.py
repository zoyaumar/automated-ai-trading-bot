"""Finnhub news headlines and earnings calendar.

Toggleable via config. Headlines are treated as untrusted data.

Failure semantics:
- get_headlines: advisory context. A failed lookup yields [] (or the last cached
  headlines) for that symbol.
- get_earnings_within_days: a risk gate. A failed or unavailable lookup OMITS the
  symbol from the result, so "unknown" is never confused with "no earnings".
  Callers should skip trading a symbol that is missing from the result.
"""

from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import finnhub

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
EARNINGS_LOOKAHEAD_DAYS = 30
_WS_RE = re.compile(r"\s+")


def _today_et() -> date:
    return datetime.now(ET).date()


class NewsClient:
    def __init__(self, api_key: str | None = None,
                 headlines_enabled: bool = True,
                 earnings_filter_enabled: bool = True,
                 max_headlines_per_symbol: int = 5,
                 max_headline_length: int = 200,
                 headline_lookback_days: int = 3,
                 headlines_cache_seconds: float = 600.0,
                 earnings_cache_seconds: float = 6 * 3600.0) -> None:
        self.api_key = api_key or os.environ.get("FINNHUB_API_KEY", "")
        self.headlines_enabled = headlines_enabled
        self.earnings_filter_enabled = earnings_filter_enabled
        self.max_headlines_per_symbol = max_headlines_per_symbol
        self.max_headline_length = max_headline_length
        self.headline_lookback_days = headline_lookback_days
        self.headlines_cache_seconds = headlines_cache_seconds
        self.earnings_cache_seconds = earnings_cache_seconds
        self._client = finnhub.Client(api_key=self.api_key) if self.api_key else None

        # symbol -> (monotonic time, headlines)
        self._headline_cache: dict[str, tuple[float, list[str]]] = {}
        # symbol -> (monotonic time, nearest upcoming earnings date or None)
        self._earnings_cache: dict[str, tuple[float, date | None]] = {}

        if self._client is None and (headlines_enabled or earnings_filter_enabled):
            logger.warning(
                "FINNHUB_API_KEY not set: headlines return nothing and earnings "
                "lookups are reported as unknown (symbols omitted)"
            )

    # --- helpers ---

    def _sanitize(self, text: Any) -> str:
        """Normalize to a single line of printable text and truncate.

        This reduces prompt-injection surface (no forged line breaks, no hidden
        control/zero-width/bidi characters) but does not eliminate it. Callers
        must still present headlines to a model as quoted, untrusted data.
        """
        if not isinstance(text, str):
            return ""
        text = unicodedata.normalize("NFKC", text)
        text = "".join(
            " " if ch.isspace() else ch
            for ch in text
            if ch.isspace() or unicodedata.category(ch)[0] != "C"
        )
        text = _WS_RE.sub(" ", text).strip()
        return text[: self.max_headline_length]

    @staticmethod
    def _log_failure(what: str, symbol: str, exc: Exception) -> bool:
        """Log without the exception message (it could contain the API key).

        Returns True if this looks like a rate limit.
        """
        status = getattr(exc, "status_code", None)
        logger.error(
            "Finnhub %s failed for %s: %s%s",
            what,
            symbol,
            type(exc).__name__,
            f" (HTTP {status})" if status else "",
        )
        return status == 429

    # --- headlines ---

    def get_headlines(self, symbols: list[str]) -> dict[str, list[str]]:
        """Return {symbol: [headline, ...]} or {} if disabled / no client."""
        if not self.headlines_enabled or self._client is None:
            return {}

        today = _today_et()
        start = today - timedelta(days=self.headline_lookback_days)
        now = time.monotonic()
        rate_limited = False
        result: dict[str, list[str]] = {}

        for symbol in symbols:
            sym = symbol.upper()
            cached = self._headline_cache.get(sym)
            if cached is not None and now - cached[0] < self.headlines_cache_seconds:
                result[symbol] = list(cached[1])
                continue
            if rate_limited:
                result[symbol] = list(cached[1]) if cached else []
                continue

            try:
                news = self._client.company_news(
                    sym, _from=start.isoformat(), to=today.isoformat()
                )
            except Exception as exc:
                rate_limited = self._log_failure("company_news", sym, exc)
                result[symbol] = list(cached[1]) if cached else []
                continue

            articles = sorted(
                (n for n in (news or []) if isinstance(n, dict)),
                key=lambda n: n.get("datetime") or 0,
                reverse=True,
            )
            seen: set[str] = set()
            headlines: list[str] = []
            for n in articles:
                h = self._sanitize(n.get("headline"))
                if not h or h.lower() in seen:
                    continue
                seen.add(h.lower())
                headlines.append(h)
                if len(headlines) >= self.max_headlines_per_symbol:
                    break

            self._headline_cache[sym] = (now, headlines)
            result[symbol] = list(headlines)
        return result

    # --- earnings ---

    def _next_earnings_date(self, sym: str, today: date) -> date | None:
        """Nearest earnings date within the lookahead window. Raises on API failure."""
        cal = self._client.earnings_calendar(  # type: ignore[union-attr]
            _from=today.isoformat(),
            to=(today + timedelta(days=EARNINGS_LOOKAHEAD_DAYS)).isoformat(),
            symbol=sym,
            international=False,
        )
        entries = cal.get("earningsCalendar", []) if isinstance(cal, dict) else cal
        upcoming: list[date] = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            raw = entry.get("date")
            if not raw:
                continue
            try:
                d = date.fromisoformat(raw)
            except (TypeError, ValueError):
                continue
            if d >= today:
                upcoming.append(d)
        return min(upcoming) if upcoming else None

    def get_earnings_within_days(self, symbols: list[str], days: int = 1) -> dict[str, int | None]:
        """Return {symbol: days_until_earnings or None}.

        The value is the number of days until the next earnings date if it falls
        within `days` (0 = today), otherwise None (nothing upcoming in the window).

        Symbols whose lookup failed, or all symbols if the filter is enabled but
        there is no API key, are OMITTED. Treat a missing symbol as "unknown" and
        skip trading it. If the filter is disabled, every symbol maps to None.
        """
        if not self.earnings_filter_enabled:
            return {s: None for s in symbols}
        if self._client is None:
            return {}

        today = _today_et()
        now = time.monotonic()
        rate_limited = False
        result: dict[str, int | None] = {}

        for symbol in symbols:
            sym = symbol.upper()
            cached = self._earnings_cache.get(sym)
            if cached is not None and now - cached[0] < self.earnings_cache_seconds:
                next_date = cached[1]
            elif rate_limited:
                continue  # unknown: omitted
            else:
                try:
                    next_date = self._next_earnings_date(sym, today)
                except Exception as exc:
                    rate_limited = self._log_failure("earnings_calendar", sym, exc)
                    continue  # unknown: omitted
                self._earnings_cache[sym] = (now, next_date)

            if next_date is None:
                result[symbol] = None
                continue
            days_until = (next_date - today).days
            result[symbol] = days_until if 0 <= days_until <= days else None
        return result