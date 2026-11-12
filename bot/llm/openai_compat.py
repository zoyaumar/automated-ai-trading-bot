"""OpenAI-compatible LLM provider (OpenRouter, Groq, Gemini, Ollama, etc.)."""

from __future__ import annotations

import os
import random
import threading
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
        self.api_key_env = api_key_env
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
        self._lock = threading.RLock()
        self._last_reset_day: str | None = None
        self._check_api_key()

    def _check_api_key(self) -> None:
        """Fail fast if the API key is missing or empty."""
        if not self.api_key:
            raise RuntimeError(
                f"'{self.api_key_env}' API key is missing or empty"
            )

    def _call_cost_usd(self, input_tokens: int | None, output_tokens: int | None) -> float:
        """Compute USD cost for a single call from token counts."""
        in_cost = 0.0
        out_cost = 0.0
        if input_tokens is not None and self.input_cost_per_mtok > 0:
            in_cost = input_tokens * self.input_cost_per_mtok / 1_000_000
        if output_tokens is not None and self.output_cost_per_mtok > 0:
            out_cost = output_tokens * self.output_cost_per_mtok / 1_000_000
        return in_cost + out_cost

    def _reset_if_new_day(self) -> None:
        """Reset daily call counter if the calendar day has rolled over."""
        today = time.strftime("%Y-%m-%d", time.localtime())
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

    def _extract_error_detail(self, resp: requests.Response) -> str:
        """Extract human-readable error detail from response body.

        Falls back to the first 200 chars of raw text when the body has no
        error.message field, so errors never produce "HTTP 404: None".
        """
        try:
            return resp.json().get("error", {}).get("message", resp.text[:200])
        except Exception:
            return resp.text[:200]

    def _wait_for_rate_limit(self, attempt: int, resp: requests.Response | None) -> None:
        """Exponential backoff with jitter, honoring Retry-After header (capped at 60s)."""
        retry_after = None
        if resp is not None:
            try:
                retry_after = float(resp.headers.get("Retry-After", ""))
            except (ValueError, TypeError):
                pass
        if retry_after is None:
            retry_after = 2 ** attempt
        retry_after = min(retry_after, 60)
        time.sleep(max(0.5, retry_after * (1 + random.uniform(-0.3, 0.3))))

    def _parse_response(self, resp: requests.Response) -> LLMResult:
        """Parse a successful LLM response into an LLMResult.

        Missing token counts return None (not 0) so callers can distinguish
        "unknown" from "zero".
        """
        try:
            data = resp.json()
            choices = data.get("choices", [])
            if not choices or not isinstance(choices, list):
                raise RuntimeError("Malformed LLM response: missing or empty 'choices'")
            message = choices[0].get("message", {})
            if not isinstance(message, dict):
                raise RuntimeError("Malformed LLM response: 'message' is not an object")
            text = message.get("content")
        except (KeyError, IndexError, ValueError, AttributeError) as exc:
            raise RuntimeError(f"Malformed LLM response: {exc}") from exc

        usage = data.get("usage")
        if not isinstance(usage, dict):
            usage = {}

        result = LLMResult(
            text=text or "",
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )
        return result

    def propose(self, system: str, user: str) -> LLMResult:
        self._reset_if_new_day()
        self._check_limits()

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

                # 200 OK — parse and return
                if resp.status_code == 200:
                    result = self._parse_response(resp)

                    # Update counters AFTER successful call (under lock)
                    with self._lock:
                        self.call_count += 1
                        if result.input_tokens is not None:
                            self.total_input_tokens += result.input_tokens
                        if result.output_tokens is not None:
                            self.total_output_tokens += result.output_tokens
                        call_cost = self._call_cost_usd(
                            result.input_tokens, result.output_tokens
                        )
                        self.total_cost_usd += call_cost
                    return result

                # Non-retryable client errors (400/401/403/404 and other 4xx except 408/429)
                if 400 <= resp.status_code < 500 and resp.status_code not in (408, 429):
                    detail = self._extract_error_detail(resp)
                    raise RuntimeError(
                        f"LLM request failed (HTTP {resp.status_code}): {detail}"
                    ) from None

                # Retryable: 408, 429, 500, 502, 503, 504
                if resp.status_code in (408, 429, 500, 502, 503, 504):
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    if attempt < self.max_retries:
                        self._wait_for_rate_limit(attempt, resp)
                        continue
                    raise RuntimeError(
                        f"LLM request failed after {self.max_retries + 1} attempts: {last_error}"
                    ) from None

                # Any other status (3xx, unknown) — raise immediately
                detail = self._extract_error_detail(resp)
                raise RuntimeError(
                    f"LLM request failed (HTTP {resp.status_code}): {detail}"
                ) from None

            except requests.RequestException as exc:
                # Network errors — retry with backoff
                last_error = str(exc)
                if attempt < self.max_retries:
                    self._wait_for_rate_limit(attempt, None)
                    continue
                raise RuntimeError(
                    f"LLM call failed after {self.max_retries + 1} attempts: {last_error}"
                ) from exc

        raise RuntimeError("LLM call failed: unknown error")
