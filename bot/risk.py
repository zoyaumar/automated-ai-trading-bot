"""Pre-trade risk firewall.

Pure functions: no network, no database. Each check returns (pass: bool, reason: str | None).
validate() runs checks in order and stops at the first failure.

Fail-closed: missing, non-finite or unknown inputs reject a BUY. CLOSE and HOLD are
never blocked by the account-level checks, so you can always exit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable


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
    open_orders: list[dict[str, Any]]   # as returned by Broker.get_account (nested legs)
    daily_start_equity: float
    high_water_mark: float


@dataclass
class MarketSnapshot:
    price: float                         # should be the LIVE ask, not the last daily close
    atr: float
    quote_age_seconds: float
    websocket_healthy: bool
    earnings_within_days: int | None     # None = nothing upcoming (only if earnings_known)
    market_open: bool
    minutes_since_open: int | None
    minutes_to_close: int | None
    # False when the earnings lookup failed / the symbol was missing from the result.
    earnings_known: bool = True


@dataclass
class Decision:
    approved: bool
    shares: int | None = None
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    reasons: list[str] | None = None


# --- helpers ---


def _reasons_list(reasons: list[str] | None, reason: str) -> list[str]:
    if reasons is None:
        reasons = []
    reasons.append(reason)
    return reasons


def _finite(*values: Any) -> bool:
    for v in values:
        try:
            if not math.isfinite(float(v)):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _num(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _round_price(price: float) -> float:
    """Same rule as Broker: whole cents at $1+, 4 decimals below."""
    return round(price, 2) if price >= 1 else round(price, 4)


def _side(order: dict[str, Any]) -> str:
    return str(order.get("side") or "").lower().split(".")[-1]


def _pending_buys(account: AccountSnapshot) -> list[dict[str, Any]]:
    """Open top-level BUY orders (entries not yet filled), with their bracket stop."""
    out: list[dict[str, Any]] = []
    for o in account.open_orders:
        if _side(o) != "buy":
            continue
        stops = [
            float(leg["stop_price"])
            for leg in (o.get("legs") or [])
            if _side(leg) == "sell" and leg.get("stop_price") is not None
        ]
        price = o.get("limit_price")
        out.append({
            "symbol": o.get("symbol"),
            "qty": _num(o.get("qty")),
            "price": float(price) if price is not None else None,
            "stop": min(stops) if stops else None,
        })
    return out


def _pending_notional(pending: list[dict[str, Any]]) -> float:
    return sum(p["qty"] * p["price"] for p in pending if p["price"] is not None)


def _open_risk(account: AccountSnapshot, pending: list[dict[str, Any]]) -> float:
    """Dollars at risk across held positions and pending entries.

    Held: qty * (avg_entry - stop), using the lowest stop first (worst case).
    Quantity with no stop counts at full cost basis. Pending: qty * (limit - stop),
    or full notional if its bracket stop isn't visible.
    """
    sell_stops: dict[str, list[tuple[float, float]]] = {}
    for o in account.open_orders:
        for order in (o, *(o.get("legs") or [])):
            if _side(order) == "sell" and order.get("stop_price") is not None:
                sell_stops.setdefault(order.get("symbol"), []).append(
                    (_num(order.get("qty")), float(order["stop_price"]))
                )

    risk = 0.0
    for sym, pos in account.positions.items():
        qty = _num(pos.get("qty"))
        avg = _num(pos.get("avg_entry_price"))
        if qty <= 0:
            continue
        remaining = qty
        for stop_qty, stop_price in sorted(sell_stops.get(sym, []), key=lambda s: s[1]):
            covered = min(remaining, stop_qty)
            risk += covered * max(0.0, avg - stop_price)
            remaining -= covered
            if remaining <= 1e-9:
                break
        if remaining > 1e-9:
            risk += remaining * avg  # unprotected: assume the whole cost basis is at risk

    for p in pending:
        if p["price"] is None:
            continue
        if p["stop"] is not None:
            risk += p["qty"] * max(0.0, p["price"] - p["stop"])
        else:
            risk += p["qty"] * p["price"]
    return risk


# --- checks ---


def check_schema_and_whitelist(proposal: Proposal, watchlist: list[str],
                               reasons: list[str] | None = None,
                               held: Iterable[str] = ()) -> tuple[bool, list[str] | None]:
    if proposal.action not in ("BUY", "CLOSE", "HOLD"):
        return False, _reasons_list(reasons, f"invalid action: {proposal.action}")
    ticker = proposal.ticker.strip().upper()
    allowed = {t.strip().upper() for t in watchlist}
    held_set = {t.strip().upper() for t in held}
    if ticker in allowed:
        return True, reasons
    # A position you still hold must stay closable even if it left the watchlist.
    if proposal.action == "CLOSE" and ticker in held_set:
        return True, reasons
    return False, _reasons_list(reasons, f"ticker not on watchlist: {proposal.ticker}")


def check_account_sane(account: AccountSnapshot,
                       reasons: list[str] | None = None) -> tuple[bool, list[str] | None]:
    if not _finite(account.equity, account.cash, account.buying_power,
                   account.daily_start_equity, account.high_water_mark):
        return False, _reasons_list(reasons, "account snapshot has missing or non-finite values")
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
    if (not _finite(proposal.stop_loss_pct, proposal.take_profit_pct)
            or proposal.stop_loss_pct <= 0 or proposal.take_profit_pct <= 0):
        return False, _reasons_list(reasons, "stop/target must be finite and > 0")
    if not _finite(entry_price, atr) or entry_price <= 0 or atr <= 0:
        return False, _reasons_list(
            reasons, f"invalid price or ATR (price={entry_price}, atr={atr})"
        )

    stop_distance_pct = proposal.stop_loss_pct
    if stop_distance_pct >= 100:
        return False, _reasons_list(reasons, f"stop {stop_distance_pct:.2f}% would be <= 0")

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
    """shares = floor((equity * risk_pct) / (entry - stop)), capped at max_position_pct.

    Levels are rounded the way the broker will round them first, and risk is sized
    from the rounded stop distance.
    """
    if proposal.action != "BUY":
        return True, None, None, None, None, reasons
    if proposal.stop_loss_pct is None or proposal.take_profit_pct is None:
        return False, None, None, None, None, _reasons_list(reasons, "missing stop/target for sizing")
    if (not _finite(equity, entry_price, proposal.stop_loss_pct, proposal.take_profit_pct)
            or equity <= 0 or entry_price <= 0):
        return False, None, None, None, None, _reasons_list(reasons, "invalid inputs for sizing")

    entry = _round_price(entry_price)
    stop = _round_price(entry_price * (1 - proposal.stop_loss_pct / 100))
    target = _round_price(entry_price * (1 + proposal.take_profit_pct / 100))
    if not (0 < stop < entry < target):
        return False, None, None, None, None, _reasons_list(
            reasons,
            f"levels invalid after rounding: stop={stop}, entry={entry}, target={target}",
        )

    risk_per_share = entry - stop
    risk_shares = math.floor((equity * (max_risk_per_trade_pct / 100)) / risk_per_share)
    position_cap_shares = math.floor(equity * (max_position_pct / 100) / entry)
    shares = min(risk_shares, position_cap_shares)

    if shares <= 0:
        return False, None, None, None, None, _reasons_list(reasons, "computed shares <= 0")
    return True, shares, entry, stop, target, reasons


def check_portfolio_limits(proposal: Proposal, account: AccountSnapshot,
                           max_open_positions: int, max_total_open_risk_pct: float,
                           reasons: list[str] | None = None,
                           new_trade_risk: float = 0.0) -> tuple[bool, list[str] | None]:
    """new_trade_risk: dollars at risk on this proposal, shares * (entry - stop)."""
    if proposal.action != "BUY":
        return True, reasons
    if proposal.ticker in account.positions:
        return False, _reasons_list(reasons, f"already long {proposal.ticker}")

    pending = _pending_buys(account)
    if any(p["symbol"] == proposal.ticker for p in pending):
        return False, _reasons_list(reasons, f"pending BUY order already open for {proposal.ticker}")
    if any(p["price"] is None for p in pending):
        return False, _reasons_list(
            reasons, "pending BUY order without a limit price; can't size open risk"
        )

    occupied = set(account.positions) | {p["symbol"] for p in pending}
    if len(occupied) >= max_open_positions:
        return False, _reasons_list(reasons, f"max open positions ({max_open_positions}) reached")

    if account.equity <= 0:
        return False, _reasons_list(reasons, "equity is zero or negative")
    total_risk_pct = (_open_risk(account, pending) + new_trade_risk) / account.equity * 100
    if total_risk_pct > max_total_open_risk_pct:
        return False, _reasons_list(
            reasons,
            f"total open risk {total_risk_pct:.2f}% > limit {max_total_open_risk_pct}%",
        )
    return True, reasons


def check_cash_floor(proposal: Proposal, account: AccountSnapshot, shares: int,
                     entry_price: float, min_cash_pct: float,
                     reasons: list[str] | None = None) -> tuple[bool, list[str] | None]:
    if proposal.action != "BUY" or shares is None:
        return True, reasons
    trade_value = shares * entry_price
    reserved = _pending_notional(_pending_buys(account))  # assumes `cash` ignores open orders
    cash_after = account.cash - reserved - trade_value
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
    # Alpaca's buying_power already reserves cash for open orders.
    if trade_value > account.buying_power:
        return False, _reasons_list(
            reasons, f"trade value ({trade_value:.2f}) > buying power ({account.buying_power:.2f})"
        )
    held_value = 0.0
    for pos in account.positions.values():
        mv = pos.get("market_value")
        held_value += abs(_num(mv)) if mv is not None else abs(
            _num(pos.get("qty")) * _num(pos.get("avg_entry_price"))
        )
    total_exposure = held_value + _pending_notional(_pending_buys(account)) + trade_value
    if total_exposure > account.equity:
        return False, _reasons_list(reasons, "total long exposure would exceed equity (no margin)")
    return True, reasons


def check_timing_and_data(market_snapshot: MarketSnapshot,
                          skip_first_minutes: int, skip_last_minutes: int,
                          max_data_age_seconds: int, earnings_within_days: int,
                          earnings_filter_enabled: bool,
                          reasons: list[str] | None = None) -> tuple[bool, list[str] | None]:
    m = market_snapshot
    if not m.market_open:
        return False, _reasons_list(reasons, "market is closed")

    if skip_first_minutes > 0:
        if m.minutes_since_open is None:
            return False, _reasons_list(reasons, "minutes since open unknown")
        if m.minutes_since_open < skip_first_minutes:
            return False, _reasons_list(
                reasons, f"within first {skip_first_minutes} minutes of open"
            )
    if skip_last_minutes > 0:
        if m.minutes_to_close is None:
            return False, _reasons_list(reasons, "minutes to close unknown")
        if m.minutes_to_close < skip_last_minutes:
            return False, _reasons_list(
                reasons, f"within last {skip_last_minutes} minutes of close"
            )

    if not m.websocket_healthy:
        return False, _reasons_list(reasons, "WebSocket not healthy")
    # Written as "not <=" so NaN is treated as stale.
    if not (m.quote_age_seconds <= max_data_age_seconds):
        return False, _reasons_list(
            reasons, f"data stale: {m.quote_age_seconds:.0f}s > {max_data_age_seconds}s max"
        )

    if earnings_filter_enabled:
        if not m.earnings_known:
            return False, _reasons_list(reasons, "earnings date unknown (lookup failed)")
        if m.earnings_within_days is not None and m.earnings_within_days <= earnings_within_days:
            return False, _reasons_list(
                reasons, f"earnings within {m.earnings_within_days} day(s)"
            )
    return True, reasons


def check_drawdown(account: AccountSnapshot, daily_loss_limit_pct: float,
                   max_drawdown_pct: float, reasons: list[str] | None = None
                   ) -> tuple[bool, list[str] | None]:
    """Note: only meaningful if high_water_mark is a persisted peak. A value that
    just equals current equity makes drawdown always 0%."""
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


# --- entry point ---


def validate(proposal: Proposal, account: AccountSnapshot, market: MarketSnapshot,
             config: dict[str, Any]) -> Decision:
    reasons: list[str] = []
    risk_cfg = config.get("risk", {})
    timing_cfg = config.get("timing", {})
    news_cfg = config.get("news", {})

    ok, reasons = check_schema_and_whitelist(
        proposal, config.get("watchlist", []), reasons, held=account.positions.keys()
    )
    if not ok:
        return Decision(approved=False, reasons=reasons)

    if proposal.action == "CLOSE":
        pos = account.positions.get(proposal.ticker)
        if pos is None or _num(pos.get("qty")) <= 0:
            return Decision(approved=False, reasons=["no existing long to close"])
        return Decision(approved=True, reasons=["close approved"])
    if proposal.action == "HOLD":
        return Decision(approved=True, reasons=["hold"])

    # --- BUY from here on ---

    ok, reasons = check_account_sane(account, reasons)
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
    if not ok_sizing or shares is None or entry is None or stop is None:
        return Decision(approved=False, reasons=reasons or ["sizing failed"])

    ok, reasons = check_portfolio_limits(
        proposal, account,
        risk_cfg.get("max_open_positions", 5),
        risk_cfg.get("max_total_open_risk_pct", 4),
        reasons,
        new_trade_risk=shares * (entry - stop),
    )
    if not ok:
        return Decision(approved=False, reasons=reasons)

    ok, reasons = check_cash_floor(
        proposal, account, shares, entry,
        risk_cfg.get("min_cash_pct", 15), reasons
    )
    if not ok:
        return Decision(approved=False, reasons=reasons)

    ok, reasons = check_buying_power(proposal, account, shares, entry, reasons)
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