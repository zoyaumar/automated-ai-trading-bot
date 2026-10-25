"""LLM interface: propose(system, user) -> result."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LLMResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMInterface:
    def propose(self, system: str, user: str) -> LLMResult:
        raise NotImplementedError