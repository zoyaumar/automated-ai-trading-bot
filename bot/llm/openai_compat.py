"""OpenAI-compatible LLM provider (OpenRouter, Groq, Gemini, Ollama, etc.)."""

from __future__ import annotations

import os
import time
from typing import Any

import requests

from .base import LLMInterface, LLMResult


class OpenAICompatLLM(LLMInterface):
    def __init__(self, base_url: str, model: str, api_key_env: str,
                 timeout_seconds: int = 60, max_retries: int = 2,
                 temperature: float = 0.2) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = os.environ.get(api_key_env, "")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.temperature = temperature

    def propose(self, system: str, user: str) -> LLMResult:
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
                text = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                return LLMResult(
                    text=text or "",
                    input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"LLM call failed after {self.max_retries + 1} attempts: {last_error}") from exc

        raise RuntimeError(f"LLM call failed: {last_error}")