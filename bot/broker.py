"""Alpaca broker integration: account, orders, clock, reconcile, bracket orders.

Note: this module also exposes market calendar helpers (Alpaca holidays and
early close detection) for downstream use by main.py and risk checks.

All market-time logic uses America/New_York. Reads retry on transient errors;
order submissions do not retry on their own. Callers must pass a deterministic
client_order_id so a retry after a timeout can't create a duplicate order
(see _submit).

Assumes a long-only bot: the unprotected-position check ignores short positions.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from typing import Any, Callable, TypeVar
from zoneinfo import ZoneInfo

from alpaca.common.exceptions import APIError
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.models import Calendar, TradeAccount
from alpaca.trading.requests import (
    GetCalendarRequest,
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)
from requests.exceptions import RequestException

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
CALENDAR_LOOKAHEAD_DAYS = 30  # default window for business-day counts
CALENDAR_CACHE_DAYS = 60  # how far ahead we fetch and cache
CALENDAR_REFRESH_SECONDS = 12 * 60 * 60
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
DEAD_ORDER_STATUSES = {"canceled", "expired", "rejected"}
CANCEL_POLL_ATTEMPTS = 10
CANCEL_POLL_INTERVAL = 0.3
UNPROTECTED_RECHECK_DELAY = 1.0

T = TypeVar("T")


# --- helpers ---


def _now_et() -> datetime:
    return datetime.now(ET)


def _enum_val(x: Any) -> Any:
    return x.value if hasattr(x, "value") else x


def _calendar_date(c: Calendar) -> date:
    return c.date.date() if isinstance(c.date, datetime) else c.date


def _round_price(price: float) -> float:
    """Alpaca rejects sub-penny increments for stocks priced at $1 or more."""
    return round(price, 2) if price >= 1 else round(price, 4)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, RequestException):
        return True
    if isinstance(exc, APIError):
        try:
            return exc.status_code in RETRYABLE_STATUS
        except Exception:
            return False
    return False


def _with_retry(fn: Callable[[], T], attempts: int = 3, base_delay: float = 0.5) -> T:
    """Retry transient failures with exponential backoff. Use for reads only.

    Note: alpaca-py's client already retries 429/504 internally, so a sustained
    429 can produce more underlying calls than `attempts`.
    """
    for i in range(attempts):
        try:
            return fn()
        except (APIError, RequestException) as exc:
            if i == attempts - 1 or not _is_retryable(exc):
                raise
            delay = base_delay * (2 ** i)
            logger.warning("Transient error (%s), retrying in %.1fs", exc, delay)
            time.sleep(delay)
    raise RuntimeError("unreachable")


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
        if not api_key or not api_secret:
            raise ValueError("API key and secret are required")
        self._trading = TradingClient(api_key, api_secret, paper=paper)
        self._data = StockHistoricalDataClient(api_key, api_secret)  # not used yet
        self.paper = paper
        self._calendar_cache: list[Calendar] | None = None
        self._calendar_window: tuple[date, date] | None = None
        self._calendar_loaded_at: datetime | None = None

    # --- internal helpers ---

    def _read(self, what: str, fn: Callable[[], T]) -> T:
        try:
            return _with_retry(fn)
        except (APIError, RequestException) as exc:
            logger.error("Failed to get %s: %s", what, exc)
            raise

    def _open_orders_request(
        self, symbol: str | None = None, nested: bool = True
    ) -> GetOrdersRequest:
        return GetOrdersRequest(
            status=QueryOrderStatus.OPEN,
            nested=nested,
            limit=500,
            symbols=[symbol] if symbol else None,
        )

    @staticmethod
    def _order_to_dict(o: Any, include_legs: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": str(o.id),
            "client_order_id": getattr(o, "client_order_id", None),
            "symbol": o.symbol,
            "side": _enum_val(o.side),
            "type": _enum_val(o.order_type),
            "status": _enum_val(o.status),
            "qty": float(o.qty) if o.qty else 0,
            "limit_price": float(o.limit_price) if o.limit_price else None,
            "stop_price": float(o.stop_price) if o.stop_price else None,
        }
        if include_legs:
            d["legs"] = [
                Broker._order_to_dict(leg, include_legs=False)
                for leg in (getattr(o, "legs", None) or [])
            ]
        return d

    # --- account ---

    def get_account(self, high_water_mark: float | None = None) -> BrokerAccount:
        """Fetch account state.

        Alpaca does not track a high-water mark. Persist it yourself and pass the
        stored value in; the returned value is max(stored, current equity), which
        you should write back.

        Note: this is three separate API calls (account, positions, orders), so a
        fill landing in between can produce a snapshot that disagrees with itself.
        """
        acct: TradeAccount = self._read("account", self._trading.get_account)
        raw_positions = self._read("positions", self._trading.get_all_positions)
        raw_orders = self._read(
            "open orders",
            lambda: self._trading.get_orders(self._open_orders_request()),
        )

        positions: dict[str, dict[str, Any]] = {}
        for p in raw_positions:
            positions[p.symbol] = {
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
            }

        open_orders = [self._order_to_dict(o) for o in raw_orders]

        equity = float(acct.equity)
        return BrokerAccount(
            equity=equity,
            cash=float(acct.cash),
            buying_power=float(acct.buying_power),
            positions=positions,
            open_orders=open_orders,
            daily_start_equity=float(acct.last_equity),
            high_water_mark=max(high_water_mark or 0.0, equity),
        )

    # --- clock ---

    def get_clock(self) -> BrokerClock:
        clock = self._read("clock", self._trading.get_clock)
        ts = clock.timestamp

        minutes_to_close: int | None = None
        minutes_since_open: int | None = None
        if clock.is_open:
            if clock.next_close:
                minutes_to_close = max(0, int((clock.next_close - ts).total_seconds() // 60))
            cal = self.get_market_calendar()
            if cal.open_time and not cal.error:
                h, m = (int(x) for x in cal.open_time.split(":"))
                open_dt = datetime.combine(ts.astimezone(ET).date(), dtime(h, m), tzinfo=ET)
                minutes_since_open = max(0, int((ts - open_dt).total_seconds() // 60))

        return BrokerClock(
            is_open=clock.is_open,
            minutes_since_open=minutes_since_open,
            minutes_to_close=minutes_to_close,
            next_open=str(clock.next_open) if clock.next_open else None,
            next_close=str(clock.next_close) if clock.next_close else None,
        )

    # --- market calendar (Alpaca holidays) ---

    def _fetch_calendar(self, start: date, end: date) -> list[Calendar]:
        """Return trading days in [start, end].

        Keeps one wide cached window (today through today + CALENDAR_CACHE_DAYS,
        widened if a caller asks for more) and slices it, so calls with different
        ranges don't evict each other. Refetches when the cache is stale, the ET
        date has rolled over, or the request falls outside the cached window.
        """
        now = datetime.now(timezone.utc)
        today = now.astimezone(ET).date()

        fresh = (
            self._calendar_cache is not None
            and self._calendar_loaded_at is not None
            and self._calendar_window is not None
            and self._calendar_window[0] <= start
            and end <= self._calendar_window[1]
            and (now - self._calendar_loaded_at).total_seconds() < CALENDAR_REFRESH_SECONDS
            and self._calendar_loaded_at.astimezone(ET).date() == today
        )

        if not fresh:
            lo = min(start, today)
            hi = max(end, today + timedelta(days=CALENDAR_CACHE_DAYS))
            calendars = _with_retry(
                lambda: self._trading.get_calendar(GetCalendarRequest(start=lo, end=hi))
            )
            self._calendar_cache = calendars
            self._calendar_window = (lo, hi)
            self._calendar_loaded_at = now

        return [c for c in self._calendar_cache if start <= _calendar_date(c) <= end]

    def get_market_calendar(self) -> MarketCalendar:
        today = _now_et().date()
        try:
            entries = self._fetch_calendar(today, today)
        except (APIError, RequestException) as exc:
            logger.error("Failed to fetch calendar: %s", exc)
            # Unknown, not a holiday. Callers should check `error`.
            return MarketCalendar(
                is_trading_day=False,
                is_holiday=False,
                open_time=None,
                close_time=None,
                is_early_close=False,
                error=f"Error fetching calendar: {exc}",
            )

        # Alpaca's calendar only lists trading days, so no entry means closed.
        if not entries:
            return MarketCalendar(
                is_trading_day=False,
                is_holiday=today.weekday() < 5,  # weekday with no session = holiday
                open_time=None,
                close_time=None,
                is_early_close=False,
            )

        today_entry = entries[0]
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
        # Alpaca's clock already accounts for weekends, holidays and early closes.
        return self.get_clock().is_open

    def get_open_business_days_remaining(
        self, start: date | None = None, days: int = CALENDAR_LOOKAHEAD_DAYS
    ) -> int:
        """Count trading days from `start` through `start + days` (inclusive)."""
        start = start or _now_et().date()
        end = start + timedelta(days=days)
        return len(self._fetch_calendar(start, end))

    # --- orders ---

    def _lookup_order(self, client_order_id: str) -> Any | None:
        try:
            return self._trading.get_order_by_client_id(client_order_id)
        except (APIError, RequestException):
            return None

    def _submit(self, request: Any, client_order_id: str, description: str) -> Any:
        """Submit an order, idempotently.

        If the submit call fails, check whether an order with this client_order_id
        already exists at the broker (timeout after the broker accepted it, or a
        retry of an earlier submit). If it exists and is still live or filled,
        return it. If it was canceled, expired or rejected, raise, so the caller
        never mistakes a dead order for a working one.
        """
        try:
            order = self._trading.submit_order(request)
        except (APIError, RequestException) as exc:
            existing = self._lookup_order(client_order_id)
            if existing is not None:
                status = str(_enum_val(existing.status)).lower()
                if status not in DEAD_ORDER_STATUSES:
                    logger.warning(
                        "Submit of %s failed (%s) but order %s exists at broker "
                        "(status=%s); returning existing order",
                        description,
                        exc,
                        client_order_id,
                        status,
                    )
                    return existing
                logger.error(
                    "Submit of %s failed (%s) and existing order %s is %s",
                    description,
                    exc,
                    client_order_id,
                    status,
                )
            else:
                logger.error("Failed to submit %s (%s): %s", description, client_order_id, exc)
            raise

        logger.info("Submitted %s (%s)", description, client_order_id)
        return order

    def submit_bracket_order(
        self,
        symbol: str,
        qty: float,
        entry_price: float,
        stop_price: float,
        target_price: float,
        client_order_id: str,
        time_in_force: TimeInForce = TimeInForce.DAY,
    ) -> Any:
        """Submit a long bracket order (limit entry, stop loss, take profit).

        client_order_id is required. Build it deterministically (e.g. symbol +
        signal id + date) so a retry after a timeout can't create a duplicate.
        """
        if not client_order_id:
            raise ValueError("client_order_id is required")

        # Bracket orders require whole shares. Floor so we never exceed the risk budget.
        whole_qty = math.floor(qty)
        if whole_qty <= 0:
            raise ValueError(f"Quantity must be >= 1 for bracket orders, got {qty}")

        entry = _round_price(entry_price)
        stop = _round_price(stop_price)
        target = _round_price(target_price)

        # Validate after rounding: that's what Alpaca actually sees.
        if not (0 < stop < entry < target):
            raise ValueError(
                f"Invalid bracket prices for {symbol} after rounding: need "
                f"0 < stop < entry < target, got stop={stop}, entry={entry}, target={target}"
            )

        request = LimitOrderRequest(
            symbol=symbol,
            qty=whole_qty,
            side=OrderSide.BUY,
            time_in_force=time_in_force,
            limit_price=entry,
            client_order_id=client_order_id,
            order_class=OrderClass.BRACKET,
            stop_loss=StopLossRequest(stop_price=stop),
            take_profit=TakeProfitRequest(limit_price=target),
        )
        description = (
            f"bracket order {symbol} BUY {whole_qty} @ {entry} "
            f"(stop {stop}, target {target})"
        )
        return self._submit(request, client_order_id, description)

    def submit_market_order(
        self,
        symbol: str,
        qty: float,
        side: OrderSide,
        client_order_id: str,
    ) -> Any:
        if not client_order_id:
            raise ValueError("client_order_id is required")
        if qty <= 0:
            raise ValueError(f"Quantity must be > 0, got {qty}")

        request = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
        )
        description = f"market order {symbol} {_enum_val(side)} {qty}"
        return self._submit(request, client_order_id, description)

    def cancel_order(self, order_id: str) -> None:
        try:
            self._trading.cancel_order_by_id(str(order_id))
            logger.info("Cancelled order %s", order_id)
        except (APIError, RequestException) as exc:
            logger.error("Failed to cancel order %s: %s", order_id, exc)
            raise

    def close_position(self, symbol: str) -> Any:
        """Cancel the symbol's open orders (bracket legs hold the shares), wait
        for the cancels to land, then close the position."""
        # nested=False so bracket legs show up as individual orders
        req = self._open_orders_request(symbol=symbol, nested=False)
        try:
            open_orders = self._read(
                f"open orders for {symbol}",
                lambda: self._trading.get_orders(req),
            )
            for o in open_orders:
                try:
                    self._trading.cancel_order_by_id(str(o.id))
                except APIError as exc:
                    # Cancelling one OCO leg can cancel the other first; not fatal.
                    logger.warning("Could not cancel order %s for %s: %s", o.id, symbol, exc)

            if open_orders:
                # Cancels are async. Poll until the shares are actually free.
                for _ in range(CANCEL_POLL_ATTEMPTS):
                    remaining = self._read(
                        f"open orders for {symbol}",
                        lambda: self._trading.get_orders(req),
                    )
                    if not remaining:
                        break
                    time.sleep(CANCEL_POLL_INTERVAL)
                else:
                    logger.error("Open orders for %s did not clear before close", symbol)
                    raise RuntimeError(f"Open orders for {symbol} did not clear before close")

            result = self._trading.close_position(symbol)
            logger.info("Closed position %s", symbol)
            return result
        except (APIError, RequestException) as exc:
            logger.error("Failed to close position %s: %s", symbol, exc)
            raise

    def get_order_by_client_id(self, client_order_id: str) -> Any:
        try:
            return self._trading.get_order_by_client_id(client_order_id)
        except (APIError, RequestException) as exc:
            logger.error("Failed to get order by client id %s: %s", client_order_id, exc)
            raise

    # --- reconcile ---

    def _find_unprotected(
        self, broker_positions: dict[str, float], qty_tolerance: float
    ) -> list[str]:
        """Long positions whose open sell-side stop quantity doesn't cover the position.

        Uses a flat (nested=False) order query so held bracket legs show up as
        individual orders, and compares quantities so a partial fill or resized
        stop is caught too.
        """
        flat = self._read(
            "open orders (flat)",
            lambda: self._trading.get_orders(self._open_orders_request(nested=False)),
        )
        stop_qty: dict[str, float] = {}
        for o in flat:
            if _enum_val(o.side) == "sell" and o.stop_price is not None:
                stop_qty[o.symbol] = stop_qty.get(o.symbol, 0.0) + float(o.qty or 0)

        return sorted(
            s
            for s, q in broker_positions.items()
            if q > 0 and stop_qty.get(s, 0.0) < q - qty_tolerance
        )

    def reconcile(
        self,
        local_positions: dict[str, float] | None = None,
        qty_tolerance: float = 1e-6,
    ) -> dict[str, Any]:
        """Fetch fresh broker state and diff it against what the bot thinks it holds.

        local_positions: {symbol: qty} from the bot's own records. If omitted, only
        the broker snapshot and the unprotected-position check are returned.

        Raises on API failure rather than returning a partial result, so a caller
        can never mistake an outage for "no positions".

        If any position looks unprotected, the snapshot is re-taken once after a
        short delay before reporting it, since a fill in flight can briefly look
        like a position with no stop.
        """
        acct = self.get_account()
        broker_positions = {s: p["qty"] for s, p in acct.positions.items()}
        unprotected = self._find_unprotected(broker_positions, qty_tolerance)

        if unprotected:
            time.sleep(UNPROTECTED_RECHECK_DELAY)
            acct = self.get_account()
            broker_positions = {s: p["qty"] for s, p in acct.positions.items()}
            unprotected = self._find_unprotected(broker_positions, qty_tolerance)

        result: dict[str, Any] = {
            "equity": acct.equity,
            "cash": acct.cash,
            "buying_power": acct.buying_power,
            "positions": broker_positions,
            "open_orders": acct.open_orders,
            "unprotected_positions": unprotected,
        }

        if local_positions is not None:
            missing_locally = {
                s: q for s, q in broker_positions.items() if s not in local_positions
            }
            missing_at_broker = {
                s: q for s, q in local_positions.items() if s not in broker_positions
            }
            qty_mismatch = {
                s: {"local": local_positions[s], "broker": broker_positions[s]}
                for s in broker_positions.keys() & local_positions.keys()
                if abs(local_positions[s] - broker_positions[s]) > qty_tolerance
            }
            result["discrepancies"] = {
                "missing_locally": missing_locally,
                "missing_at_broker": missing_at_broker,
                "qty_mismatch": qty_mismatch,
            }
            result["in_sync"] = not (missing_locally or missing_at_broker or qty_mismatch)

        return result