"""Alpaca broker integration: account, orders, clock, reconcile, bracket orders.

Note: this module also exposes market calendar helpers (Alpaca holidays and
early close detection) for downstream use by main.py and risk checks.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import LimitOrderRequest, MarketOrderRequest, StopLossRequest, TakeProfitRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.models import Calendar, Position, TradeAccount


@dataclass
class BrokerAccount:
    equity: float
    cash: float
    buying_power: float
    positions: dict[str, dict[str, Any]]
    open_orders: list[dict[str, Any]]
    daily_start_equity: float
    high_water_mark: float


@dataclass
class BrokerClock:
    is_open: bool
    minutes_since_open: int | None
    minutes_to_close: int | None
    next_open: str | None
    next_close: str | None


@dataclass
class MarketCalendar:
    is_trading_day: bool
    is_holiday: bool
    open_time: str | None
    close_time: str | None
    is_early_close: bool
    early_close_reason: str | None = None
    error: str | None = None


class Broker:
    def __init__(self, api_key: str, api_secret: str, paper: bool = True) -> None:
        self._trading = TradingClient(api_key, api_secret, paper=paper)
        self._data = StockHistoricalDataClient(api_key, api_secret)
        self.paper = paper
        self._calendar_cache: list[Calendar] | None = None
        self._calendar_loaded_at: datetime | None = None

    # --- account ---

    def get_account(self) -> BrokerAccount:
        acct: TradeAccount = self._trading.get_account()
        positions: dict[str, dict[str, Any]] = {}
        for p in self._trading.get_positions():
            positions[p.symbol] = {
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
            }

        open_orders: list[dict[str, Any]] = []
        for o in self._trading.get_orders():
            status = getattr(o, "status", None)
            status_str = status.value if hasattr(status, "value") else str(status)
            if status_str != "open":
                continue
            open_orders.append({
                "id": o.id,
                "symbol": o.symbol,
                "side": o.side,
                "type": o.order_type,
                "qty": float(o.qty) if o.qty else 0,
                "limit_price": float(o.limit_price) if o.limit_price else None,
                "stop_price": float(o.stop_price) if o.stop_price else None,
            })
        equity = float(acct.equity)
        return BrokerAccount(
            equity=equity,
            cash=float(acct.cash),
            buying_power=float(acct.buying_power),
            positions=positions,
            open_orders=open_orders,
            daily_start_equity=float(acct.equity),
            high_water_mark=equity,
        )

    # --- clock ---

    def get_clock(self) -> BrokerClock:
        clock = self._trading.get_clock()
        return BrokerClock(
            is_open=clock.is_open,
            minutes_since_open=None,
            minutes_to_close=None,
            next_open=str(clock.next_open) if clock.next_open else None,
            next_close=str(clock.next_close) if clock.next_close else None,
        )

    # --- market calendar (Alpaca holidays) ---

    def _fetch_calendar(self) -> list[Calendar]:
        if self._calendar_cache is not None:
            return self._calendar_cache
        calendars = self._trading.get_calendar()
        self._calendar_cache = calendars
        self._calendar_loaded_at = datetime.now(timezone.utc)
        return calendars

    def get_market_calendar(self) -> MarketCalendar:
        today = date.today()
        try:
            calendars = self._fetch_calendar()
        except APIError as exc:
            return MarketCalendar(
                is_trading_day=False,
                is_holiday=True,
                open_time=None,
                close_time=None,
                is_early_close=False,
                error=f"APIError fetching calendar: {exc}",
            )

        today_entry = None
        for c in calendars:
            cal_date = c.date.date() if hasattr(c.date, "date") else c.date
            if cal_date == today:
                today_entry = c
                break

        if today_entry is None:
            return MarketCalendar(
                is_trading_day=True,
                is_holiday=False,
                open_time="09:30",
                close_time="16:00",
                is_early_close=False,
            )

        open_time = today_entry.open.strftime("%H:%M") if today_entry.open else None
        close_time = today_entry.close.strftime("%H:%M") if today_entry.close else None

        is_early = False
        early_reason = None
        if close_time and close_time < "16:00":
            is_early = True
            early_reason = f"Early close at {close_time}"

        return MarketCalendar(
            is_trading_day=True,
            is_holiday=False,
            open_time=open_time,
            close_time=close_time,
            is_early_close=is_early,
            early_close_reason=early_reason,
        )

    def is_market_open(self) -> bool:
        clock = self.get_clock()
        cal = self.get_market_calendar()
        if cal.error:
            return clock.is_open
        if not cal.is_trading_day:
            return False
        if cal.is_early_close:
            close_dt = datetime.strptime(cal.close_time, "%H:%M").time()
            now = datetime.now().time()
            if now >= close_dt:
                return False
        return clock.is_open

    def get_open_business_days_remaining(self, start: date | None = None) -> int:
        start = start or date.today()
        calendars = self._fetch_calendar()
        count = 0
        for c in calendars:
            cal_date = c.date.date() if hasattr(c.date, "date") else c.date
            if cal_date >= start and cal_date <= date.today() + __import__("datetime").timedelta(days=30):
                count += 1
        return count

