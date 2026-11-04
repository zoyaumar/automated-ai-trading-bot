"""OpenAI-compatible LLM provider (OpenRouter, Groq, Gemini, Ollama, etc.)."""

from __future__ import annotations

import os
import time
from typing import Any

import requests

from .base import LLMInterface, LLMResult


class OpenAICompatLLM(LLMInterface):
    def __init__(
        self, base_url: str, model: str, api_key_env: str,
        timeout_seconds: int = 60, max_retries: int = 2,
        temperature: float = 0.2, max_calls_per_day: int | None = None,
        input_cost_per_mtok: float = 0.0, output_cost_per_mtok: float = 0.0,
        monthly_budget_usd: float = 0.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = os.environ.get(api_key_env, "")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.temperature = temperature
        self.max_calls_per_day = max_calls_per_day
        self.input_cost_per_mtok = input_cost_per_mtok
        self.output_cost_per_mtok = output_cost_per_mtok
        self.monthly_budget_usd = monthly_budget_usd
        self.call_count: int = 0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_cost_usd: float = 0.0

    def _call_cost_usd(self, input_tokens: int | None, output_tokens: int | None) -> float:
        """Compute USD cost for a single call from token counts."""
        in_cost = 0.0
        out_cost = 0.0
        if input_tokens is not None and self.input_cost_per_mtok > 0:
            in_cost = input_tokens * self.input_cost_per_mtok / 1_000_000
        if output_tokens is not None and self.output_cost_per_mtok > 0:
            out_cost = output_tokens * self.output_cost_per_mtok / 1_000_000
        return in_cost + out_cost

    def propose(self, system: str, user: str) -> LLMResult:
        if self.max_calls_per_day is not None and self.call_count >= self.max_calls_per_day:
            raise RuntimeError(
                f"Daily LLM call limit reached ({self.call_count}/{self.max_calls_per_day})"
            )
        if self.monthly_budget_usd > 0 and self.total_cost_usd >= self.monthly_budget_usd:
            raise RuntimeError(
                f"Monthly LLM budget reached (${self.total_cost_usd:.4f}/${self.monthly_budget_usd})"
            )
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }

        last_error: str | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)

                # Client errors (400/401/403) are not retryable — raise immediately
                if resp.status_code in (400, 401, 403):
                    try:
                        detail = resp.json().get("error", {}).get("message", resp.text[:200])
                    except Exception:
                        detail = resp.text[:200]
                    raise RuntimeError(
                        f"LLM request failed (HTTP {resp.status_code}): {detail}"
                    ) from None

                # Retryable server errors
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    if attempt < self.max_retries:
                        time.sleep(2 ** attempt)
                        continue
                    raise RuntimeError(
                        f"LLM request failed after {self.max_retries + 1} attempts: {last_error}"
                    ) from None

                # Any other status — raise for non-2xx, proceed for 200
                resp.raise_for_status()

                # Parse response, converting malformed payloads to RuntimeError
                try:
                    data = resp.json()
                    text = data["choices"][0]["message"]["content"]
                except (KeyError, IndexError, ValueError) as exc:
                    raise RuntimeError(f"Malformed LLM response: {exc}") from exc

                usage = data.get("usage", {})
                result = LLMResult(
                    text=text or "",
                    input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                )

                # Update counters AFTER successful call
                self.call_count += 1
                if result.input_tokens is not None:
                    self.total_input_tokens += result.input_tokens
                if result.output_tokens is not None:
                    self.total_output_tokens += result.output_tokens
                call_cost = self._call_cost_usd(result.input_tokens, result.output_tokens)
                self.total_cost_usd += call_cost
                return result
            except requests.RequestException as exc:
                last_error = str(exc)
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(
                    f"LLM call failed after {self.max_retries + 1} attempts: {last_error}"
                ) from exc

        raise RuntimeError("LLM call failed: unknown error")
