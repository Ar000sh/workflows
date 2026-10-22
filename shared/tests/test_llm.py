import pytest

from shared.llm import cost_usd


def test_cost_for_haiku_per_million_tokens():
    assert cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000) == pytest.approx(6.0)


def test_cost_for_sonnet_small_call():
    assert cost_usd("claude-sonnet-5-5", 1_000, 500) == pytest.approx(0.007)


def test_cost_matches_dated_model_ids():
    # The API may report the full dated ID even when we asked for the alias.
    assert cost_usd("claude-haiku-4-5-20251001", 1_000_000, 0) == pytest.approx(1.0)


def test_cost_unknown_model_is_none():
    assert cost_usd("some-other-model", 100, 100) is None


from types import SimpleNamespace

from pydantic import BaseModel

from shared.llm import LLMClient, LLMError
from shared.testing import (
    FakeAnthropic, api_error, connection_error, fake_response, timeout_error,
)


class Answer(BaseModel):
    ok: bool


def make_client(outcomes):
    fake = FakeAnthropic(outcomes)
    sleeps: list[float] = []
    return LLMClient(fake, sleep=sleeps.append), fake, sleeps


def parse_once(client):
    return client.parse(model="claude-haiku-4-5", system="sys", user="hi", output_model=Answer)


def test_parse_success_first_try():
    client, fake, sleeps = make_client([fake_response(parsed=Answer(ok=True))])
    result = parse_once(client)
    assert result.value == Answer(ok=True)
    assert result.attempts == 1
    assert result.input_tokens == 100 and result.output_tokens == 20
    assert result.cost_usd == pytest.approx((100 * 1.0 + 20 * 5.0) / 1_000_000)
    assert sleeps == []
    call = fake.messages.calls[0]
    assert call["output_format"] is Answer
    assert call["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.parametrize("error", [connection_error(), timeout_error(), api_error(500), api_error(429)])
def test_retryable_errors_are_retried(error):
    client, fake, sleeps = make_client([error, fake_response(parsed=Answer(ok=True))])
    result = parse_once(client)
    assert result.attempts == 2
    assert sleeps == [1.0]


def test_gives_up_after_three_retries_with_backoff():
    client, fake, sleeps = make_client([api_error(500)] * 4)
    with pytest.raises(LLMError):
        parse_once(client)
    assert len(fake.messages.calls) == 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_bad_request_is_not_retried():
    client, fake, sleeps = make_client([api_error(400)])
    with pytest.raises(LLMError):
        parse_once(client)
    assert len(fake.messages.calls) == 1
    assert sleeps == []


def test_missing_parsed_output_is_retried():
    client, fake, sleeps = make_client([fake_response(parsed=None), fake_response(parsed=Answer(ok=True))])
    assert parse_once(client).attempts == 2


def test_refusal_is_not_retried():
    client, fake, sleeps = make_client([fake_response(parsed=None, stop_reason="refusal")])
    with pytest.raises(LLMError, match="refused"):
        parse_once(client)
    assert len(fake.messages.calls) == 1


def test_max_tokens_stop_is_not_retried():
    client, fake, sleeps = make_client([fake_response(parsed=None, stop_reason="max_tokens")])
    with pytest.raises(LLMError, match="max_tokens"):
        parse_once(client)
    assert len(fake.messages.calls) == 1 and sleeps == []


def test_text_joins_text_blocks_and_skips_thinking():
    content = [
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text="Hallo "),
        SimpleNamespace(type="text", text="Welt"),
    ]
    client, fake, _ = make_client([fake_response(content=content, model="claude-sonnet-5-5")])
    result = client.text(model="claude-sonnet-5-5", system="sys", user="hi")
    assert result.value == "Hallo Welt"


def test_text_with_fallbacks_and_effort_uses_beta_endpoint():
    client, fake, _ = make_client([fake_response(text="ok", model="claude-sonnet-5-5")])
    client.text(model="claude-sonnet-5-5", system="sys", user="hi", effort="low", fallbacks=True)
    call = fake.messages.calls[0]
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"
    assert call["output_config"] == {"effort": "low"}


def test_empty_text_is_retried():
    client, fake, _ = make_client([fake_response(text=""), fake_response(text="ok")])
    assert client.text(model="claude-sonnet-5-5", system="s", user="u").attempts == 2


import anthropic
import httpx2

_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def test_api_response_validation_error_is_not_retried():
    response = httpx2.Response(200, request=_REQUEST)
    error = anthropic.APIResponseValidationError(response=response, body=None)
    client, fake, sleeps = make_client([error])
    with pytest.raises(LLMError):
        parse_once(client)
    assert len(fake.messages.calls) == 1
    assert sleeps == []


def test_non_retryable_failure_emits_warning_log(caplog):
    client, fake, _ = make_client([api_error(400)])
    with pytest.raises(LLMError):
        parse_once(client)
    assert any("non-retryable API error 400" in record.message for record in caplog.records)


def test_unknown_cost_for_served_model_emits_warning(caplog):
    client, _, _ = make_client([fake_response(parsed=Answer(ok=True), model="mystery-model-1")])
    result = parse_once(client)
    assert result.cost_usd is None
    assert any("cost" in r.message and "mystery-model-1" in r.message for r in caplog.records)
