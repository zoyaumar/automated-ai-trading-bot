"""Load and validate config.yaml + .env.

Pydantic settings model with range checks and cross-checks.
Refuses to start on any failure, naming the bad key.
Logs a config hash at startup.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class LLMProviderConfig(BaseModel):
    name: str
    provider: str  # openai_compat | anthropic
    base_url: str
    model: str
    api_key_env: str


class LLMConfig(BaseModel):
    providers: list[LLMProviderConfig]
    timeout_seconds: int = 60
    max_calls_per_day: int = 50
    monthly_budget_usd: float = 0.0
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0

    @model_validator(mode="after")
    def _validate_providers(self) -> LLMConfig:
        if not self.providers:
            raise ValueError("llm.providers must not be empty")
        return self


class RiskConfig(BaseModel):
    max_risk_per_trade_pct: float = Field(ge=0, le=100)
    max_position_pct: float = Field(ge=0, le=100)
    max_open_positions: int = Field(ge=0)
    max_total_open_risk_pct: float = Field(ge=0, le=100)
    min_cash_pct: float = Field(ge=0, le=100)
    daily_loss_limit_pct: float = Field(ge=0, le=100)
    max_drawdown_pct: float = Field(ge=0, le=100)
    min_stop_atr_multiple: float = Field(ge=0)
    max_stop_pct: float = Field(ge=0, le=100)
    min_reward_to_risk: float = Field(ge=0)

    @model_validator(mode="after")
    def _cross_checks(self) -> RiskConfig:
        if self.max_position_pct + self.min_cash_pct > 100:
            raise ValueError("max_position_pct + min_cash_pct must be <= 100")
        if self.max_risk_per_trade_pct > self.max_total_open_risk_pct:
            raise ValueError("max_risk_per_trade_pct must be <= max_total_open_risk_pct")
        return self


class TimingConfig(BaseModel):
    skip_first_minutes: int = Field(ge=0)
    skip_last_minutes: int = Field(ge=0)
    max_data_age_seconds: int = Field(ge=0)
    cancel_unfilled_entry_minutes: int = Field(ge=0)


class NewsConfig(BaseModel):
    headlines_enabled: bool
    earnings_filter_enabled: bool


class ExecutionConfig(BaseModel):
    entry_limit_buffer_pct: float = Field(ge=0)


class MonitoringConfig(BaseModel):
    heartbeat_minutes: int = Field(ge=0)
    alert_after_minutes: int = Field(ge=0)


class BotConfig(BaseModel):
    mode: str = Field(pattern="^(paper|live)$")
    cycle_minutes: int = Field(ge=1)
    safety_check_minutes: int = Field(ge=1)
    watchlist: list[str]
    risk: RiskConfig
    timing: TimingConfig
    news: NewsConfig
    llm: LLMConfig
    execution: ExecutionConfig
    monitoring: MonitoringConfig

    @field_validator("watchlist")
    @classmethod
    def _watchlist_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("watchlist must not be empty")
        return v


class Settings:
    """Loaded configuration with validation and hash."""

    def __init__(self, config_path: str | Path = "config.yaml") -> None:
        self.config_path = Path(config_path)
        self._raw: dict[str, Any]
        self.config: BotConfig
        self.config_hash: str
        self._load()

    def _load(self) -> None:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        with open(self.config_path, "r", encoding="utf-8") as fh:
            self._raw = yaml.safe_load(fh)
        if not isinstance(self._raw, dict):
            raise ValueError("config.yaml must contain a mapping at the top level")
        self.config = BotConfig.model_validate(self._raw)
        # Deterministic hash of the raw config for audit traceability
        canonical = json.dumps(self._raw, sort_keys=True).encode("utf-8")
        self.config_hash = hashlib.sha256(canonical).hexdigest()[:16]

    @property
    def as_dict(self) -> dict[str, Any]:
        return self.config.model_dump()