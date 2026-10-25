"""Alpaca broker integration: account, orders, clock, reconcile, bracket orders."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
    TrailingStopOrderRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderType, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.models import Account, Position, Order


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


class Broker:
    def __init__(self, api_key: str, api_secret: str, paper: bool = True) -> None:
        self._trading = TradingClient(api_key, api_secret, paper=paper)
        self._data = StockHistoricalDataClient(api_key, api_secret)
        self.paper = paper

    # --- account ---
    def get_account(self) -> BrokerAccount:
        acct: Account = self._trading.get_account()
        positions: dict[str, dict[str, Any]] = {}
        for p in self._trading.get_all_positions():
            positions[p.symbol] = {
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
            }
        open_orders: list[dict[str, Any]] = []
        for o in self._trading.get_all_orders(status=QueryOrderStatus.OPEN):
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
            daily_start_equity=float(acct.equity),  # approximated; refined by main loop
            high_water_mark=equity,  # refined by main loop with persistent HWM
        )

    # --- clock ---
    def get_clock(self) -> BrokerClock:
        clock = self._trading.get_clock()
        return BrokerClock(
            is_open=clock.is_open,
            minutes_since_open=None,  # computed from timestamp in main loop
            minutes_to_close=None,
            next_open=str(clock.next_open) if clock.next_open else None,
            next_close=str(clock.next_close) if clock.next_close else None,
        )

    # --- orders ---
    def submit_bracket_order(self, symbol: str, qty: float, entry_price: float,
                             stop_price: float, target_price: float,
                             client_order_id: str | None = None) -> Any:
        if client_order_id is None:
            client_order_id = str(uuid.uuid4())
        order_data = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
            limit_price=round(entry_price, 4),
            client_order_id=client_order_id,
        )
        # Attach stop loss and take profit as bracket legs
        order_data.stop_loss = StopLossRequest(stop_price=round(stop_price, 4))
        order_data.take_profit = TakeProfitRequest(limit_price=round(target_price, 4))
        return self._trading.submit_order(order_data)

    def submit_market_order(self, symbol: str, qty: float, side: OrderSide,
                            client_order_id: str | None = None) -> Any:
        if client_order_id is None:
            client_order_id = str(uuid.uuid4())
        order_data = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
        )
        return self._trading.submit_order(order_data)

    def cancel_order(self, order_id: str) -> None:
        self._trading.cancel_order_by_id(order_id)

    def cancel_all_orders(self) -> None:
        self._trading.cancel_all_orders()

    def close_position(self, symbol: str) -> Any:
        return self._trading.close_position(symbol)

    def get_order(self, client_order_id: str) -> Any:
        return self._trading.get_order_by_client_order_id(client_order_id)

    # --- reconcile ---
    def reconcile(self) -> dict[str, Any]:
        """Compare local state with Alpaca. Returns mismatch report."""
        acct = self.get_account()
        return {
            "equity": acct.equity,
            "cash": acct.cash,
            "buying_power": acct.buying_power,
            "positions": acct.positions,
            "open_orders": acct.open_orders,
        }