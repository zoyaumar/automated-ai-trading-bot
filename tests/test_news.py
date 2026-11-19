"""Tests for Finnhub news headlines and earnings calendar."""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock
import pytest

from bot.news import NewsClient, _today_et


# --- Sanitization tests ---

def test_sanitize_strips_control_chars() -> None:
    client = NewsClient(api_key="fake", headlines_enabled=True)
    dirty = "Hello\x00\x1f\u200b\u202eWorld"
    clean = client._sanitize(dirty)
    assert "\x00" not in clean
    assert "\x1f" not in clean
    assert "\u200b" not in clean
    assert "\u202e" not in clean
    assert clean == "Hello World"


def test_sanitize_normalizes_unicode() -> None:
    client = NewsClient(api_key="fake", headlines_enabled=True)
    assert client._sanitize("ＡＡＰＬ") == "AAPL"


def test_sanitize_truncates() -> None:
    client = NewsClient(api_key="fake", headlines_enabled=True, max_headline_length=10)
    assert len(client._sanitize("A" * 20)) == 10


def test_sanitize_non_string_returns_empty() -> None:
    client = NewsClient(api_key="fake", headlines_enabled=True)
    assert client._sanitize(None) == ""
    assert client._sanitize(123) == ""
    assert client._sanitize(["list"]) == ""


def test_sanitize_collapses_whitespace() -> None:
    client = NewsClient(api_key="fake", headlines_enabled=True)
    assert client._sanitize("Multiple   spaces\tand\nnewlines") == "Multiple spaces and newlines"


# --- Headlines caching tests ---

def test_get_headlines_returns_cached() -> None:
    client = NewsClient(api_key="fake", headlines_enabled=True, headlines_cache_seconds=3600)
    mock_client = MagicMock()
    mock_client.company_news.return_value = [
        {"headline": "News 1", "datetime": int(datetime.now().timestamp())},
        {"headline": "News 2", "datetime": int(datetime.now().timestamp())},
    ]
    client._client = mock_client

    r1 = client.get_headlines(["AAPL"])
    r2 = client.get_headlines(["AAPL"])
    assert r1 == r2
    assert mock_client.company_news.call_count == 1


def test_get_headlines_refreshes_after_ttl() -> None:
    client = NewsClient(api_key="fake", headlines_enabled=True, headlines_cache_seconds=0.1)
    mock_client = MagicMock()
    mock_client.company_news.side_effect = [
        [{"headline": "Old", "datetime": int(datetime.now().timestamp())}],
        [{"headline": "New", "datetime": int(datetime.now().timestamp())}],
    ]
    client._client = mock_client

    r1 = client.get_headlines(["AAPL"])
    import time; time.sleep(0.2)
    r2 = client.get_headlines(["AAPL"])
    assert r1["AAPL"][0] == "Old"
    assert r2["AAPL"][0] == "New"
    assert mock_client.company_news.call_count == 2


def test_get_headlines_dedupes_by_lowercase() -> None:
    client = NewsClient(api_key="fake", headlines_enabled=True, max_headlines_per_symbol=5)
    mock_client = MagicMock()
    mock_client.company_news.return_value = [
        {"headline": "Apple announces earnings", "datetime": int(datetime.now().timestamp())},
        {"headline": "APPLE ANNOUNCES EARNINGS", "datetime": int(datetime.now().timestamp())},
        {"headline": "apple announces earnings", "datetime": int(datetime.now().timestamp())},
    ]
    client._client = mock_client
    assert len(client.get_headlines(["AAPL"])["AAPL"]) == 1


def test_get_headlines_disabled_returns_empty() -> None:
    client = NewsClient(api_key="fake", headlines_enabled=False)
    assert client.get_headlines(["AAPL"]) == {}


def test_get_headlines_handles_malformed_entries() -> None:
    client = NewsClient(api_key="fake", headlines_enabled=True)
    mock_client = MagicMock()
    mock_client.company_news.return_value = [
        {"headline": "Valid", "datetime": int(datetime.now().timestamp())},
        "not a dict",
        {"datetime": int(datetime.now().timestamp())},
        {"headline": 123, "datetime": int(datetime.now().timestamp())},
    ]
    client._client = mock_client
    assert client.get_headlines(["AAPL"])["AAPL"] == ["Valid"]


# --- Earnings tests ---

def test_get_earnings_disabled_returns_none_dict() -> None:
    client = NewsClient(api_key="fake", earnings_filter_enabled=False)
    assert client.get_earnings_within_days(["AAPL", "MSFT"], days=5) == {"AAPL": None, "MSFT": None}


def test_get_earnings_no_client_returns_empty(monkeypatch) -> None:
    """get_earnings returns empty dict when no API key and enabled."""
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    client = NewsClient(api_key="", earnings_filter_enabled=True)
    assert client.get_earnings_within_days(["AAPL"], days=5) == {}


def test_get_earnings_omits_on_failure() -> None:
    client = NewsClient(api_key="fake", earnings_filter_enabled=True)
    client._client = MagicMock()
    client._client.earnings_calendar.side_effect = Exception("API error")
    r = client.get_earnings_within_days(["AAPL", "MSFT"], days=5)
    assert "AAPL" not in r and "MSFT" not in r


def test_get_earnings_returns_days_until() -> None:
    client = NewsClient(api_key="fake", earnings_filter_enabled=True)
    client._client = MagicMock()
    client._client.earnings_calendar.return_value = {"earningsCalendar": [
        {"date": (date.today() + timedelta(days=2)).isoformat(), "symbol": "AAPL"},
    ]}
    assert client.get_earnings_within_days(["AAPL"], days=5)["AAPL"] == 2


def test_get_earnings_returns_none_outside_window() -> None:
    client = NewsClient(api_key="fake", earnings_filter_enabled=True)
    client._client = MagicMock()
    client._client.earnings_calendar.return_value = {"earningsCalendar": [
        {"date": (date.today() + timedelta(days=10)).isoformat(), "symbol": "AAPL"},
    ]}
    assert client.get_earnings_within_days(["AAPL"], days=5)["AAPL"] is None


def test_get_earnings_returns_none_no_upcoming() -> None:
    client = NewsClient(api_key="fake", earnings_filter_enabled=True)
    client._client = MagicMock()
    client._client.earnings_calendar.return_value = {"earningsCalendar": []}
    assert client.get_earnings_within_days(["AAPL"], days=5)["AAPL"] is None


def test_get_earnings_caches_results() -> None:
    client = NewsClient(api_key="fake", earnings_filter_enabled=True, earnings_cache_seconds=3600)
    client._client = MagicMock()
    client._client.earnings_calendar.return_value = {"earningsCalendar": [
        {"date": (date.today() + timedelta(days=1)).isoformat(), "symbol": "AAPL"},
    ]}
    r1 = client.get_earnings_within_days(["AAPL"], days=5)
    r2 = client.get_earnings_within_days(["AAPL"], days=5)
    assert r1 == r2
    assert client._client.earnings_calendar.call_count == 1


def test_get_earnings_handles_malformed_entries() -> None:
    """get_earnings skips malformed calendar entries per symbol."""
    client = NewsClient(api_key="fake", earnings_filter_enabled=True)
    # Mock _next_earnings_date directly since earnings_calendar is called per symbol
    client._next_earnings_date = MagicMock(side_effect=[
        date.today() + timedelta(days=1),  # AAPL
        None,  # MSFT - missing date
        None,  # NVDA - invalid date
    ])
    r = client.get_earnings_within_days(["AAPL", "MSFT", "NVDA"], days=5)
    assert r["AAPL"] == 1
    assert r.get("MSFT") is None
    assert r.get("NVDA") is None


def test_get_earnings_rate_limited_stops_querying() -> None:
    client = NewsClient(api_key="fake", earnings_filter_enabled=True)
    client._client = MagicMock()
    client._client.earnings_calendar.side_effect = [
        {"earningsCalendar": [{"date": date.today().isoformat(), "symbol": "AAPL"}]},
        Exception("429 Too Many Requests"),
    ]
    r = client.get_earnings_within_days(["AAPL", "MSFT"], days=5)
    assert "AAPL" in r and "MSFT" not in r


def test_today_et_returns_date() -> None:
    assert isinstance(_today_et(), date)


def test_sanitize_handles_bidi_override() -> None:
    client = NewsClient(api_key="fake", headlines_enabled=True)
    clean = client._sanitize("Normal\u202eReversed")
    assert "\u202e" not in clean and "\u202d" not in clean


def test_sanitize_handles_zero_width_joiner() -> None:
    client = NewsClient(api_key="fake", headlines_enabled=True)
    assert client._sanitize("A\u200dB") == "AB"


def test_earnings_lookahead_window() -> None:
    from bot.news import EARNINGS_LOOKAHEAD_DAYS
    assert EARNINGS_LOOKAHEAD_DAYS == 30