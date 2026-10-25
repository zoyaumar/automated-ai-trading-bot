"""Tests for config loading and validation."""

from bot.config import Settings


def test_config_range_checks(settings: Settings) -> None:
    cfg = settings.config
    assert cfg.mode == "paper"
    assert cfg.watchlist == ["AAPL", "MSFT", "NVDA", "AMZN", "SPY", "QQQ"]
    assert cfg.risk.max_risk_per_trade_pct == 2
    assert cfg.risk.max_position_pct == 15
    assert settings.config_hash  # non-empty hash


def test_config_hash_deterministic(settings: Settings, tmp_config) -> None:
    h1 = settings.config_hash
    s2 = Settings(tmp_config)
    assert h1 == s2.config_hash