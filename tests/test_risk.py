"""Tests for the risk firewall — one test per check with pass/fail cases."""

from __future__ import annotations

from bot.risk import (
    Proposal, AccountSnapshot, MarketSnapshot, validate,
    check_schema_and_whitelist, check_stop_and_target_bounds,
    check_cash_floor, check_buying_power, check_timing_and_data,
    check_drawdown, check_portfolio_limits, size_position,
)


def _base_config() -> dict:
    return {
        "watchlist": ["AAPL", "MSFT", "NVDA", "AMZN", "SPY", "QQQ"],
        "risk": {
            "max_risk_per_trade_pct": 2,
            "max_position_pct": 15,
            "max_open_positions": 5,
            "max_total_open_risk_pct": 4,
            "min_cash_pct": 15,
            "daily_loss_limit_pct": 5,
            "max_drawdown_pct": 15,
            "min_stop_atr_multiple": 1.5,
            "max_stop_pct": 8,
            "min_reward_to_risk": 1.5,
        },
        "timing": {
            "skip_first_minutes": 15,
            "skip_last_minutes": 30,
            "max_data_age_seconds": 120,
            "earnings_skip_trading_days": 1,
        },
        "news": {"earnings_filter_enabled": True},
    }


def _base_account() -> AccountSnapshot:
    return AccountSnapshot(
        equity=100_000.0,
        cash=50_000.0,
        buying_power=100_000.0,
        positions={},
        open_orders=[],
        daily_start_equity=100_000.0,
        high_water_mark=100_000.0,
    )


def _base_market() -> MarketSnapshot:
    return MarketSnapshot(
        price=150.0,
        atr=5.0,
        quote_age_seconds=5.0,
        websocket_healthy=True,
        earnings_within_days=None,
        market_open=True,
        minutes_since_open=60,
        minutes_to_close=120,
    )


def test_schema_and_whitelist_pass() -> None:
    ok, reasons = check_schema_and_whitelist(
        Proposal(action="BUY", ticker="AAPL"), ["AAPL", "MSFT"]
    )
    assert ok is True


def test_schema_and_whitelist_bad_action() -> None:
    ok, reasons = check_schema_and_whitelist(
        Proposal(action="SHORT", ticker="AAPL"), ["AAPL"]
    )
    assert ok is False
    assert reasons is not None


def test_schema_and_whitelist_off_watchlist() -> None:
    ok, reasons = check_schema_and_whitelist(
        Proposal(action="BUY", ticker="TSLA"), ["AAPL"]
    )
    assert ok is False
    assert reasons is not None


def test_stop_target_bounds_pass() -> None:
    ok, reasons = check_stop_and_target_bounds(
        Proposal(action="BUY", ticker="AAPL", stop_loss_pct=5, take_profit_pct=10),
        entry_price=150.0, atr=5.0,
        min_stop_atr_multiple=1.5, max_stop_pct=8, min_reward_to_risk=1.5,
    )
    assert ok is True


def test_stop_too_tight() -> None:
    ok, reasons = check_stop_and_target_bounds(
        Proposal(action="BUY", ticker="AAPL", stop_loss_pct=0.5, take_profit_pct=10),
        entry_price=150.0, atr=5.0,
        min_stop_atr_multiple=1.5, max_stop_pct=8, min_reward_to_risk=1.5,
    )
def test_stop_too_wide() -> None:
    ok, reasons = check_stop_and_target_bounds(
        Proposal(action="BUY", ticker="AAPL", stop_loss_pct=10, take_profit_pct=20),
        entry_price=150.0, atr=5.0,
        min_stop_atr_multiple=1.5, max_stop_pct=8, min_reward_to_risk=1.5,
    )
    assert ok is False


def test_reward_to_risk_too_low() -> None:
    ok, reasons = check_stop_and_target_bounds(
        Proposal(action="BUY", ticker="AAPL", stop_loss_pct=5, take_profit_pct=5),
        entry_price=150.0, atr=5.0,
        min_stop_atr_multiple=1.5, max_stop_pct=8, min_reward_to_risk=1.5,
    )
    assert ok is False


def test_sizing_pass() -> None:
    ok, shares, entry, stop, target, reasons = size_position(
        Proposal(action="BUY", ticker="AAPL", stop_loss_pct=5, take_profit_pct=10),
        equity=100_000.0, atr=5.0, entry_price=150.0,
        max_risk_per_trade_pct=2, max_position_pct=15,
    )
    assert ok is True
    assert shares is not None and shares > 0


def test_sizing_zero_shares() -> None:
    ok, shares, entry, stop, target, reasons = size_position(
        Proposal(action="BUY", ticker="AAPL", stop_loss_pct=8, take_profit_pct=10),
        equity=100.0, atr=5.0, entry_price=150.0,
        max_risk_per_trade_pct=2, max_position_pct=15,
    )
    assert ok is False


def test_cash_floor_pass() -> None:
    ok, reasons = check_cash_floor(
        Proposal(action="BUY", ticker="AAPL"), _base_account(),
        shares=100, entry_price=150.0, min_cash_pct=15,
    )
    assert ok is True


def test_cash_floor_fail() -> None:
    ok, reasons = check_cash_floor(
        Proposal(action="BUY", ticker="AAPL"), _base_account(),
        shares=1000, entry_price=150.0, min_cash_pct=15,
    )
    assert ok is False


def test_buying_power_pass() -> None:
    ok, reasons = check_buying_power(
        Proposal(action="BUY", ticker="AAPL"), _base_account(),
        shares=100, entry_price=150.0,
    )
    assert ok is True


def test_buying_power_fail() -> None:
    acct = AccountSnapshot(
        equity=100_000.0, cash=10_000.0, buying_power=5_000.0,
        positions={}, open_orders=[], daily_start_equity=100_000.0,
        high_water_mark=100_000.0,
    )
    ok, reasons = check_buying_power(
        Proposal(action="BUY", ticker="AAPL"), acct, shares=100, entry_price=150.0,
    )
    assert ok is False
def test_timing_market_closed() -> None:
    mkt = _base_market()
    mkt.market_open = False
    ok, reasons = check_timing_and_data(mkt, 15, 30, 120, 1, True)
    assert ok is False


def test_timing_stale_data() -> None:
    mkt = _base_market()
    mkt.quote_age_seconds = 200.0
    ok, reasons = check_timing_and_data(mkt, 15, 30, 120, 1, True)
    assert ok is False


def test_timing_websocket_down() -> None:
    mkt = _base_market()
    mkt.websocket_healthy = False
    ok, reasons = check_timing_and_data(mkt, 15, 30, 120, 1, True)
    assert ok is False


def test_drawdown_daily_loss() -> None:
    acct = AccountSnapshot(
        equity=94_000.0, cash=50_000.0, buying_power=100_000.0,
        positions={}, open_orders=[], daily_start_equity=100_000.0,
        high_water_mark=100_000.0,
    )
    ok, reasons = check_drawdown(acct, 5.0, 15.0)
    assert ok is False


def test_drawdown_max_drawdown() -> None:
    acct = AccountSnapshot(
        equity=84_000.0, cash=50_000.0, buying_power=100_000.0,
        positions={}, open_orders=[], daily_start_equity=100_000.0,
        high_water_mark=100_000.0,
    )
    ok, reasons = check_drawdown(acct, 5.0, 15.0)
    assert ok is False


def test_drawdown_ok() -> None:
    ok, reasons = check_drawdown(_base_account(), 5.0, 15.0)
    assert ok is True


def test_portfolio_limits_duplicate() -> None:
    acct = AccountSnapshot(
        equity=100_000.0, cash=50_000.0, buying_power=100_000.0,
        positions={"AAPL": {"qty": 10, "avg_entry_price": 150.0}},
        open_orders=[], daily_start_equity=100_000.0, high_water_mark=100_000.0,
    )
    ok, reasons = check_portfolio_limits(
        Proposal(action="BUY", ticker="AAPL"), acct, 5, 4.0,
    )
    assert ok is False


def test_validate_full_pass() -> None:
    decision = validate(
        Proposal(action="BUY", ticker="AAPL", stop_loss_pct=5, take_profit_pct=10),
        _base_account(), _base_market(), _base_config(),
    )
    assert decision.approved is True


def test_validate_off_watchlist() -> None:
    decision = validate(
        Proposal(action="BUY", ticker="TSLA", stop_loss_pct=5, take_profit_pct=10),
        _base_account(), _base_market(), _base_config(),
    )
    assert decision.approved is False


def test_validate_hold() -> None:
    decision = validate(
        Proposal(action="HOLD", ticker="AAPL"),
        _base_account(), _base_market(), _base_config(),
    )
    assert decision.approved is True
    assert decision.reasons == ["hold"]