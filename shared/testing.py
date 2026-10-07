"""Test doubles for code that talks to Claude. Used by every track's tests."""
from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx2

from shared.llm import LLMResult

_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def api_error(status: int) -> anthropic.APIStatusError:
    """An SDK error as the real client would raise it for this HTTP status."""
    response = httpx2.Response(status, request=_REQUEST)
    if status == 400:
        cls = anthropic.BadRequestError
    elif status == 429:
        cls = anthropic.RateLimitError
    elif status >= 500:
        cls = anthropic.InternalServerError
    else:
        cls = anthropic.APIStatusError
    return cls(f"HTTP {status}", response=response, body=None)


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=_REQUEST)


def timeout_error() -> anthropic.APITimeoutError:
    return anthropic.APITimeoutError(request=_REQUEST)


def fake_response(
    *,
    parsed=None,
    text: str = "",
    content: list | None = None,
    model: str = "claude-haiku-4-5",
    stop_reason: str = "end_turn",
    input_tokens: int = 100,
    output_tokens: int = 20,
):
    """Looks enough like an SDK Message for LLMClient to consume."""
    if content is None:
        content = [SimpleNamespace(type="text", text=text)] if text else []
    return SimpleNamespace(
        parsed_output=parsed,
        content=content,
        model=model,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class _ScriptedMessages:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls: list[dict] = []

    def _next(self, kwargs):
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def parse(self, **kwargs):
        return self._next(kwargs)

    def create(self, **kwargs):
        return self._next(kwargs)


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: returns or raises scripted outcomes in order."""

    def __init__(self, outcomes):
        self.messages = _ScriptedMessages(outcomes)
        self.beta = SimpleNamespace(messages=self.messages)


class FakeLLM:
    """Stands in for LLMClient at the level classifiers and drafters use it."""

    def __init__(self, *, parse=(), text=()):
        self._parse = list(parse)
        self._text = list(text)
        self.parse_calls: list[dict] = []
        self.text_calls: list[dict] = []

    def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        return _as_result(self._parse.pop(0), kwargs)

    def text(self, **kwargs):
        self.text_calls.append(kwargs)
        return _as_result(self._text.pop(0), kwargs)


def _as_result(outcome, kwargs):
    if isinstance(outcome, BaseException):
        raise outcome
    return LLMResult(
        value=outcome, model=kwargs["model"], input_tokens=100, output_tokens=20,
        cost_usd=0.001, attempts=1,
    )
