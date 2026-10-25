"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from bot.config import Settings


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    """Write a minimal config.yaml to a temp dir and return its path."""
    config = {
        "mode": "paper",
        "cycle_minutes": 30,
        "safety_check_minutes": 5,
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
            "cancel_unfilled_entry_minutes": 10,
            "earnings_skip_trading_days": 1,
        },
        "news": {
            "headlines_enabled": False,
            "earnings_filter_enabled": True,
        },
        "llm": {
            "providers": [
                {
                    "name": "test-provider",
                    "provider": "openai_compat",
                    "base_url": "https://example.com/v1",
                    "model": "test-model",
                    "api_key_env": "TEST_API_KEY",
                }
            ],
            "timeout_seconds": 60,
            "max_calls_per_day": 50,
            "monthly_budget_usd": 0,
            "input_cost_per_mtok": 0,
            "output_cost_per_mtok": 0,
        },
        "execution": {"entry_limit_buffer_pct": 0.1},
        "monitoring": {"heartbeat_minutes": 5, "alert_after_minutes": 15},
    }
    import yaml
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(config))
    return p


@pytest.fixture
def settings(tmp_config: Path) -> Settings:
    return Settings(tmp_config)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent accidental network calls in tests."""
    monkeypatch.setenv("ALPACA_PAPER_KEY", "")
    monkeypatch.setenv("ALPACA_PAPER_SECRET", "")
    monkeypatch.setenv("FINNHUB_API_KEY", "")
    monkeypatch.setenv("FRED_API_KEY", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    monkeypatch.setenv("HEARTBEAT_URL", "")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")