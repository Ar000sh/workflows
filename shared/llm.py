"""Thin wrapper around the Anthropic SDK, reused by every track.

It owns three things so callers don't have to:
- retries: up to 3 retries with exponential backoff (1s, 2s, 4s)
- output: structured output parsed into a Pydantic model, or plain text
- cost: token usage turned into US dollars
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

import anthropic
import pydantic

log = logging.getLogger(__name__)

T = TypeVar("T")
M = TypeVar("M", bound=pydantic.BaseModel)

# USD per million tokens: (input, output). Source: claude-api skill model table (cached 2026-09-25).
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}

# Server-side fallback: if a model declines, the API re-runs the request on a suitable model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


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


class _UnusableOutput(Exception):
    """The model answered, but not in a usable shape. Worth another attempt."""


class LLMClient:
    def __init__(
        self,
        client=None,
        *,
        max_retries: int = 3,
        base_delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        # SDK retries are off: this class is the single place the retry policy lives.
        self._client = client if client is not None else anthropic.Anthropic(max_retries=0, timeout=60.0)
        self.max_retries = max_retries
        self.base_delay = base_delay
        self._sleep = sleep

    def parse(self, *, model: str, system: str, user: str, output_model: type[M], max_tokens: int = 1024) -> LLMResult[M]:
        """One call whose answer is validated into `output_model` (structured output)."""

        def call():
            response = self._client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=output_model,
            )
            _check_stop_reason(response)
            if response.parsed_output is None:
                raise _UnusableOutput("no parsed output")
            return response, response.parsed_output

        return self._with_retries(call)

    def text(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int = 4096,
        effort: str | None = None,
        fallbacks: bool = False,
    ) -> LLMResult[str]:
        """One call that returns plain text (thinking blocks are skipped)."""

        def call():
            kwargs = dict(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            if effort:
                kwargs["output_config"] = {"effort": effort}
            if fallbacks:
                response = self._client.beta.messages.create(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                response = self._client.messages.create(**kwargs)
            _check_stop_reason(response)
            text = "".join(block.text for block in response.content if block.type == "text").strip()
            if not text:
                raise _UnusableOutput("empty text response")
            return response, text

        return self._with_retries(call)

    def _with_retries(self, call):
        attempts = self.max_retries + 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response, value = call()
            except (_UnusableOutput, pydantic.ValidationError) as error:
                last_error = error
            except anthropic.APIStatusError as error:
                if error.status_code == 429 or error.status_code >= 500:
                    last_error = error
                else:
                    log.warning("LLM non-retryable API error %d: %s", error.status_code, error.message)
                    raise LLMError(f"non-retryable API error {error.status_code}: {error.message}") from error
            except anthropic.APIConnectionError as error:  # includes timeouts
                last_error = error
            except anthropic.APIError as error:
                log.warning("LLM unexpected API error: %r", error)
                raise LLMError(f"unexpected API error: {error!r}") from error
            else:
                usage = response.usage
                cost = cost_usd(response.model, usage.input_tokens, usage.output_tokens)
                if cost is None:
                    log.warning("LLM cost unknown for served model %r (no price entry)", response.model)
                return LLMResult(
                    value=value,
                    model=response.model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_usd=cost,
                    attempts=attempt,
                )
            log.warning("LLM attempt %d/%d failed: %r", attempt, attempts, last_error)
            if attempt < attempts:
                self._sleep(self.base_delay * 2 ** (attempt - 1))
        raise LLMError(f"gave up after {attempts} attempts: {last_error!r}") from last_error


def _check_stop_reason(response) -> None:
    # Retrying these would give the same answer again, so they fail immediately.
    if response.stop_reason == "refusal":
        log.warning("LLM model refused the request")
        raise LLMError("model refused the request")
    if response.stop_reason == "max_tokens":
        log.warning("LLM response hit max_tokens")
        raise LLMError("response hit max_tokens; raise the limit")
