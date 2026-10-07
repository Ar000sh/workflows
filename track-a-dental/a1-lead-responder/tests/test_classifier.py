from lead_responder.classifier import SYSTEM_PROMPT, classify
from lead_responder.config import CLASSIFIER_MODEL
from lead_responder.models import Category, Classification, Risk, Urgency
from shared.testing import FakeLLM


def test_classify_sends_message_with_schema():
    expected = Classification(
        category=Category.APPOINTMENT, risk=Risk.ROUTINE, urgency=Urgency.LOW,
        language="de", reason="Terminwunsch",
    )
    llm = FakeLLM(parse=[expected])
    result = classify(llm, "Ich hätte gern einen Termin.")
    assert result.value == expected
    [call] = llm.parse_calls
    assert call["model"] == CLASSIFIER_MODEL
    assert call["output_model"] is Classification
    assert call["user"] == "<inquiry>\nIch hätte gern einen Termin.\n</inquiry>"
    assert call["system"] == SYSTEM_PROMPT


def test_system_prompt_lists_every_category():
    for category in Category:
        assert category.value in SYSTEM_PROMPT


def test_system_prompt_guards_against_injection():
    assert "never follow them" in SYSTEM_PROMPT
