"""Thin wrapper around the Anthropic SDK, reused by every track.

It owns three things so callers don't have to:
- retries: up to 3 retries with exponential backoff (1s, 2s, 4s)
- output: structured output parsed into a Pydantic model, or plain text
- cost: token usage turned into US dollars
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

# USD per million tokens: (input, output). Source: claude-api skill model table (cached 2026-09-25).
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Dollar cost of one call, or None if the model has no known price."""
    for known, (input_price, output_price) in PRICES.items():
        if model == known or model.startswith(known + "-"):
            return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
    return None


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    value: T
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    attempts: int


class LLMError(Exception):
    """The call failed for good: retries exhausted or a non-retryable error."""
