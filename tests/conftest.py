"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from bot.config import Settings


def pytest_configure(config):
    """Load .env at session start so real-model tests can use API keys."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if key and val and key not in os.environ:
                os.environ[key] = val


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
    """Prevent accidental network calls in tests. Preserve real LLM keys if present."""
    # Only clear if not already set (e.g., loaded from .env via pytest_configure)
    for key in [
        "ALPACA_PAPER_KEY", "ALPACA_PAPER_SECRET",
        "FINNHUB_API_KEY", "FRED_API_KEY",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "HEARTBEAT_URL",
    ]:
        if key not in os.environ:
            monkeypatch.setenv(key, "")
    # Preserve LLM keys if they exist (for real-model tests)
    for key in ["NVIDIA_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY"]:
        if key not in os.environ:
            monkeypatch.setenv(key, "")