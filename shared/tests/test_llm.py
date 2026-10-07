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
