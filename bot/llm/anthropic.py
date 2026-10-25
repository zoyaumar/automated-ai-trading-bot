"""Anthropic Claude LLM provider (same interface as OpenAI-compatible)."""

from __future__ import annotations

import os
import time
from typing import Any

import requests

from .base import LLMInterface, LLMResult


class AnthropicLLM(LLMInterface):
    def __init__(self, api_key_env: str = "ANTHROPIC_API_KEY",
                 timeout_seconds: int = 60, max_retries: int = 2,
                 temperature: float = 0.2) -> None:
        self.api_key = os.environ.get(api_key_env, "")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.temperature = temperature
        self.model = "claude-sonnet-5-5"  # configured via config.yaml providers list
        self.base_url = "https://api.anthropic.com/v1"

    def propose(self, system: str, user: str) -> LLMResult:
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
            "max_tokens": 1000,
        }

        last_error: str | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload,
                                     timeout=self.timeout_seconds)
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
                return LLMResult(
                    text=text,
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Anthropic call failed after {self.max_retries + 1} attempts: {last_error}") from exc

        raise RuntimeError(f"Anthropic call failed: {last_error}")