"""Anthropic Claude LLM provider (same interface as OpenAI-compatible)."""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import requests

from .base import LLMInterface, LLMResult


class AnthropicLLM(LLMInterface):
    def __init__(
        self,
        api_key_env: str = "ANTHROPIC_API_KEY",
        timeout_seconds: int = 60,
        max_retries: int = 2,
        temperature: float = 0.2,
        # Accept the same kwargs that get_llm() forwards from config so this
        # provider is a drop-in replacement for OpenAICompatLLM.
        model: str = "claude-opus-4-5",
        base_url: str = "https://api.anthropic.com/v1",
        max_calls_per_day: int | None = None,
        input_cost_per_mtok: float = 0.0,
        output_cost_per_mtok: float = 0.0,
        monthly_budget_usd: float = 0.0,
    ) -> None:
        self.api_key_env = api_key_env
        self.api_key = os.environ.get(api_key_env, "")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.temperature = temperature
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_calls_per_day = max_calls_per_day
        self.input_cost_per_mtok = input_cost_per_mtok
        self.output_cost_per_mtok = output_cost_per_mtok
        self.monthly_budget_usd = monthly_budget_usd

        # Same counters as OpenAICompatLLM for consistency.
        self.call_count: int = 0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_cost_usd: float = 0.0
        self._lock = threading.RLock()
        self._last_reset_day: str | None = None

        self._check_api_key()

    # --- guards (mirrors OpenAICompatLLM) ---

    def _check_api_key(self) -> None:
        """Fail fast if the API key is missing or empty."""
        if not self.api_key:
            raise RuntimeError(
                f"'{self.api_key_env}' API key is missing or empty"
            )

    def _call_cost_usd(self, input_tokens: int | None, output_tokens: int | None) -> float:
        in_cost = 0.0
        out_cost = 0.0
        if input_tokens is not None and self.input_cost_per_mtok > 0:
            in_cost = input_tokens * self.input_cost_per_mtok / 1_000_000
        if output_tokens is not None and self.output_cost_per_mtok > 0:
            out_cost = output_tokens * self.output_cost_per_mtok / 1_000_000
        return in_cost + out_cost

    def _reset_if_new_day(self) -> None:
        """Reset daily call counter if the calendar day has rolled over (UTC)."""
        today = time.strftime("%Y-%m-%d", time.gmtime())
        with self._lock:
            if self._last_reset_day != today:
                self.call_count = 0
                self.total_input_tokens = 0
                self.total_output_tokens = 0
                self.total_cost_usd = 0.0
                self._last_reset_day = today

    def _check_limits(self) -> None:
        """Enforce daily call limit and monthly budget under lock."""
        with self._lock:
            if self.max_calls_per_day is not None and self.call_count >= self.max_calls_per_day:
                raise RuntimeError(
                    f"Daily LLM call limit reached ({self.call_count}/{self.max_calls_per_day})"
                )
            if self.monthly_budget_usd > 0 and self.total_cost_usd >= self.monthly_budget_usd:
                raise RuntimeError(
                    f"Monthly LLM budget reached (${self.total_cost_usd:.4f}/${self.monthly_budget_usd})"
                )

    # --- request ---

    def propose(self, system: str, user: str) -> LLMResult:
        self._reset_if_new_day()
        self._check_limits()

        url = f"{self.base_url}/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": self.temperature,
            "max_tokens": 1024,
        }

        last_error: str | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=self.timeout_seconds
                )
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    if attempt < self.max_retries:
                        time.sleep(2 ** attempt)
                        continue
                    resp.raise_for_status()
                resp.raise_for_status()
                data = resp.json()
                text = "".join(block.get("text", "") for block in data.get("content", []))
                usage = data.get("usage", {})
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")

                result = LLMResult(
                    text=text,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                with self._lock:
                    self.call_count += 1
                    if input_tokens is not None:
                        self.total_input_tokens += input_tokens
                    if output_tokens is not None:
                        self.total_output_tokens += output_tokens
                    self.total_cost_usd += self._call_cost_usd(input_tokens, output_tokens)
                return result

            except requests.RequestException as exc:
                last_error = str(exc)
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(
                    f"Anthropic call failed after {self.max_retries + 1} attempts: {last_error}"
                ) from exc

        raise RuntimeError(f"Anthropic call failed: {last_error}")