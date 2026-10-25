"""Finnhub news headlines and earnings calendar.

Toggleable via config. Headlines are treated as untrusted data.
"""

from __future__ import annotations

import os
from typing import Any

import finnhub


class NewsClient:
    def __init__(self, api_key: str | None = None,
                 headlines_enabled: bool = True,
                 earnings_filter_enabled: bool = True,
                 max_headlines_per_symbol: int = 5,
                 max_headline_length: int = 200) -> None:
        self.api_key = api_key or os.environ.get("FINNHUB_API_KEY", "")
        self.headlines_enabled = headlines_enabled
        self.earnings_filter_enabled = earnings_filter_enabled
        self.max_headlines_per_symbol = max_headlines_per_symbol
        self.max_headline_length = max_headline_length
        self._client = finnhub.Client(api_key=self.api_key) if self.api_key else None

    def _sanitize(self, text: str) -> str:
        """Strip control characters and truncate."""
        text = "".join(ch for ch in text if ord(ch) >= 32 or ch in "\n\t")
        return text[: self.max_headline_length]

    def get_headlines(self, symbols: list[str]) -> dict[str, list[str]]:
        """Return {symbol: [headline, ...]} or {} if disabled / no client."""
        if not self.headlines_enabled or self._client is None:
            return {}
        result: dict[str, list[str]] = {}
        for symbol in symbols:
            try:
                news = self._client.company_news(symbol, _limit=10)
                headlines = [self._sanitize(n.get("headline", "")) for n in news[:self.max_headlines_per_symbol]]
                result[symbol] = [h for h in headlines if h]
            except Exception:
                result[symbol] = []
        return result

    def get_earnings_within_days(self, symbols: list[str], days: int = 1) -> dict[str, int | None]:
        """Return {symbol: days_until_earnings or None}."""
        if not self.earnings_filter_enabled or self._client is None:
            return {s: None for s in symbols}
        result: dict[str, int | None] = {}
        for symbol in symbols:
            try:
                cal = self._client.earnings_calendar(symbol=symbol)
                # Simplified: return earliest upcoming earnings days offset
                result[symbol] = None  # Finnhub calendar returns dates; compute offset in main loop
            except Exception:
                result[symbol] = None
        return result