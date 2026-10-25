"""Pre-trade risk firewall.

Pure functions: no network, no database. Each check returns (pass: bool, reason: str | None).
validate() runs checks in order and stops at the first failure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass
class Proposal:
    action: str          # BUY | CLOSE | HOLD
    ticker: str
    confidence: float | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    reasoning: str | None = None


@dataclass
class AccountSnapshot:
    equity: float
    cash: float
    buying_power: float
    positions: dict[str, dict[str, Any]]
    open_orders: list[dict[str, Any]]
    daily_start_equity: float
    high_water_mark: float


@dataclass
class MarketSnapshot:
    price: float
    atr: float
    quote_age_seconds: float
    websocket_healthy: bool
    earnings_within_days: int | None
    market_open: bool
    minutes_since_open: int | None
    minutes_to_close: int | None


@dataclass
class Decision:
    approved: bool
    shares: int | None = None
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    reasons: list[str] | None = None


def _reasons_list(reasons: list[str] | None, reason: str) -> list[str]:
    if reasons is None:
        reasons = []
    reasons.append(reason)
    return reasons


def check_schema_and_whitelist(proposal: Proposal, watchlist: list[str],
                                reasons: list[str] | None = None) -> tuple[bool, list[str] | None]:
    if proposal.action not in ("BUY", "CLOSE", "HOLD"):
        return False, _reasons_list(reasons, f"invalid action: {proposal.action}")
    if proposal.ticker not in watchlist:
        return False, _reasons_list(reasons, f"ticker not on watchlist: {proposal.ticker}")
    return True, reasons


def check_stop_and_target_bounds(proposal: Proposal, entry_price: float, atr: float,
                                  min_stop_atr_multiple: float, max_stop_pct: float,
                                  min_reward_to_risk: float,
                                  reasons: list[str] | None = None) -> tuple[bool, list[str] | None]:
    if proposal.action != "BUY":
        return True, reasons
    if proposal.stop_loss_pct is None:
        return False, _reasons_list(reasons, "missing stop_loss_pct")
    if proposal.take_profit_pct is None:
        return False, _reasons_list(reasons, "missing take_profit_pct")

    stop_distance_pct = proposal.stop_loss_pct
    min_stop_pct = (min_stop_atr_multiple * atr / entry_price) * 100
    if stop_distance_pct < min_stop_pct:
        return False, _reasons_list(
            reasons, f"stop {stop_distance_pct:.2f}% < min {min_stop_pct:.2f}% "
                     f"({min_stop_atr_multiple}x ATR)"
        )
    if stop_distance_pct > max_stop_pct:
        return False, _reasons_list(
            reasons, f"stop {stop_distance_pct:.2f}% > max {max_stop_pct}%"
        )

    min_tp_pct = min_reward_to_risk * stop_distance_pct
    if proposal.take_profit_pct < min_tp_pct:
        return False, _reasons_list(
            reasons, f"take-profit {proposal.take_profit_pct:.2f}% < min {min_tp_pct:.2f}% "
                     f"({min_reward_to_risk}x stop)"
        )
    return True, reasons
def size_position(proposal: Proposal, equity: float, atr: float, entry_price: float,
                  max_risk_per_trade_pct: float, max_position_pct: float,
                  reasons: list[str] | None = None) -> tuple[bool, int | None, float | None,
                                                             float | None, float | None,
                                                             list[str] | None]:
    """shares = floor((equity * risk_pct) / stop_distance), capped at max_position_pct."""
    if proposal.action != "BUY" or proposal.stop_loss_pct is None:
        return True, None, None, None, None, reasons

    stop_distance = entry_price * (proposal.stop_loss_pct / 100)
    if stop_distance <= 0:
        return False, None, None, None, None, _reasons_list(reasons, "stop distance must be > 0")

    risk_shares = math.floor((equity * (max_risk_per_trade_pct / 100)) / stop_distance)
    max_position_value = equity * (max_position_pct / 100)
    position_cap_shares = math.floor(max_position_value / entry_price)
    shares = min(risk_shares, position_cap_shares)

    if shares <= 0:
        return False, None, None, None, None, _reasons_list(reasons, "computed shares <= 0")

    entry = round(entry_price, 4)
    stop = round(entry_price * (1 - proposal.stop_loss_pct / 100), 4)
    target = round(entry_price * (1 + (proposal.take_profit_pct or 0) / 100), 4)
    return True, shares, entry, stop, target, reasons


def check_portfolio_limits(proposal: Proposal, account: AccountSnapshot,
                           max_open_positions: int, max_total_open_risk_pct: float,
                           reasons: list[str] | None = None) -> tuple[bool, list[str] | None]:
    if proposal.action != "BUY":
        return True, reasons
    if proposal.ticker in account.positions:
        return False, _reasons_list(reasons, f"already long {proposal.ticker}")
    if len(account.positions) >= max_open_positions:
        return False, _reasons_list(reasons, f"max open positions ({max_open_positions}) reached")
    return True, reasons


def check_cash_floor(proposal: Proposal, account: AccountSnapshot, shares: int,
                     entry_price: float, min_cash_pct: float,
                     reasons: list[str] | None = None) -> tuple[bool, list[str] | None]:
    if proposal.action != "BUY" or shares is None:
        return True, reasons
    trade_value = shares * entry_price
    cash_after = account.cash - trade_value
    min_cash = account.equity * (min_cash_pct / 100)
    if cash_after < min_cash:
        return False, _reasons_list(
            reasons, f"cash after trade ({cash_after:.2f}) < min cash floor ({min_cash:.2f})"
        )
    return True, reasons


def check_buying_power(proposal: Proposal, account: AccountSnapshot, shares: int,
                       entry_price: float, reasons: list[str] | None = None
                       ) -> tuple[bool, list[str] | None]:
    if proposal.action != "BUY" or shares is None:
        return True, reasons
    trade_value = shares * entry_price
    if trade_value > account.buying_power:
        return False, _reasons_list(
            reasons, f"trade value ({trade_value:.2f}) > buying power ({account.buying_power:.2f})"
        )
    total_exposure = sum(
        pos.get("qty", 0) * pos.get("avg_entry_price", 0) for pos in account.positions.values()
    ) + trade_value
    if total_exposure > account.equity:
        return False, _reasons_list(reasons, "total long exposure would exceed equity (no margin)")
    return True, reasons
def check_timing_and_data(market_snapshot: MarketSnapshot,
                          skip_first_minutes: int, skip_last_minutes: int,
                          max_data_age_seconds: int, earnings_within_days: int,
                          earnings_filter_enabled: bool,
                          reasons: list[str] | None = None) -> tuple[bool, list[str] | None]:
    if not market_snapshot.market_open:
        return False, _reasons_list(reasons, "market is closed")
    if market_snapshot.minutes_since_open is not None:
        if market_snapshot.minutes_since_open < skip_first_minutes:
            return False, _reasons_list(
                reasons, f"within first {skip_first_minutes} minutes of open"
            )
        if market_snapshot.minutes_to_close is not None:
            if market_snapshot.minutes_to_close < skip_last_minutes:
                return False, _reasons_list(
                    reasons, f"within last {skip_last_minutes} minutes of close"
                )
    if not market_snapshot.websocket_healthy:
        return False, _reasons_list(reasons, "WebSocket not healthy")
    if market_snapshot.quote_age_seconds > max_data_age_seconds:
        return False, _reasons_list(
            reasons, f"data stale: {market_snapshot.quote_age_seconds:.0f}s > "
                     f"{max_data_age_seconds}s max"
        )
    if earnings_filter_enabled and market_snapshot.earnings_within_days is not None:
        if market_snapshot.earnings_within_days <= earnings_within_days:
            return False, _reasons_list(
                reasons, f"earnings within {market_snapshot.earnings_within_days} day(s)"
            )
    return True, reasons
def check_drawdown(account: AccountSnapshot, daily_loss_limit_pct: float,
                   max_drawdown_pct: float, reasons: list[str] | None = None
                   ) -> tuple[bool, list[str] | None]:
    if account.equity <= 0:
        return False, _reasons_list(reasons, "equity is zero or negative")
    if account.daily_start_equity > 0:
        daily_loss_pct = ((account.daily_start_equity - account.equity) /
                          account.daily_start_equity) * 100
        if daily_loss_pct >= daily_loss_limit_pct:
            return False, _reasons_list(
                reasons, f"daily loss {daily_loss_pct:.2f}% >= limit {daily_loss_limit_pct}%"
            )
    if account.high_water_mark > 0:
        drawdown_pct = ((account.high_water_mark - account.equity) /
                        account.high_water_mark) * 100
        if drawdown_pct >= max_drawdown_pct:
            return False, _reasons_list(
                reasons, f"drawdown {drawdown_pct:.2f}% >= limit {max_drawdown_pct}%"
            )
    return True, reasons


def validate(proposal: Proposal, account: AccountSnapshot, market: MarketSnapshot,
             config: dict[str, Any]) -> Decision:
    reasons: list[str] = []
    risk_cfg = config.get("risk", {})
    timing_cfg = config.get("timing", {})
    news_cfg = config.get("news", {})

    ok, reasons = check_schema_and_whitelist(
        proposal, config.get("watchlist", []), reasons
    )
    if not ok:
        return Decision(approved=False, reasons=reasons)

    if proposal.action == "CLOSE":
        if proposal.ticker not in account.positions:
            return Decision(approved=False, reasons=["no existing long to close"])
        return Decision(approved=True, reasons=["close approved"])
    if proposal.action == "HOLD":
        return Decision(approved=True, reasons=["hold"])

    entry_price = market.price
    atr = market.atr

    ok, reasons = check_stop_and_target_bounds(
        proposal, entry_price, atr,
        risk_cfg.get("min_stop_atr_multiple", 1.5),
        risk_cfg.get("max_stop_pct", 8),
        risk_cfg.get("min_reward_to_risk", 1.5),
        reasons,
    )
    if not ok:
        return Decision(approved=False, reasons=reasons)

    ok_sizing, shares, entry, stop, target, reasons = size_position(
        proposal, account.equity, atr, entry_price,
        risk_cfg.get("max_risk_per_trade_pct", 2),
        risk_cfg.get("max_position_pct", 15),
        reasons,
    )
    if not ok_sizing or shares is None:
        return Decision(approved=False, reasons=reasons or ["sizing failed"])

    ok, reasons = check_portfolio_limits(
        proposal, account,
        risk_cfg.get("max_open_positions", 5),
        risk_cfg.get("max_total_open_risk_pct", 4),
        reasons,
    )
    if not ok:
        return Decision(approved=False, reasons=reasons)

    ok, reasons = check_cash_floor(
        proposal, account, shares, entry,
        risk_cfg.get("min_cash_pct", 15), reasons
    )
    if not ok:
        return Decision(approved=False, reasons=reasons)

    ok, reasons = check_buying_power(
        proposal, account, shares, entry, reasons
    )
    if not ok:
        return Decision(approved=False, reasons=reasons)

    ok, reasons = check_timing_and_data(
        market,
        timing_cfg.get("skip_first_minutes", 15),
        timing_cfg.get("skip_last_minutes", 30),
        timing_cfg.get("max_data_age_seconds", 120),
        timing_cfg.get("earnings_skip_trading_days", 1),
        news_cfg.get("earnings_filter_enabled", True),
        reasons,
    )
    if not ok:
        return Decision(approved=False, reasons=reasons)

    ok, reasons = check_drawdown(
        account,
        risk_cfg.get("daily_loss_limit_pct", 5),
        risk_cfg.get("max_drawdown_pct", 15),
        reasons,
    )
    if not ok:
        return Decision(approved=False, reasons=reasons)

    return Decision(
        approved=True,
        shares=shares,
        entry=entry,
        stop=stop,
        target=target,
        reasons=reasons if reasons else ["approved"],
    )
    reasons: list[str] | None = None