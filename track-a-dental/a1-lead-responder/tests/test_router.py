import pytest

from lead_responder.models import Category, Classification, Risk, Urgency
from lead_responder.router import (
    ALWAYS_SENSITIVE, Action, decide, downgrade_failed_auto_reply, normalize_language,
)


def cls(category, risk="routine", language="de"):
    return Classification(
        category=Category(category), risk=Risk(risk), urgency=Urgency.NORMAL,
        language=language, reason="test",
    )


def test_failed_classification_goes_to_manual_review_with_everything_safe():
    decision = decide(None)
    assert decision.risk is Risk.SENSITIVE
    assert decision.actions == (Action.ACKNOWLEDGMENT,)
    assert decision.language == "de+en"
    assert decision.emergency is True  # unknown content: include emergency info to be safe
    assert decision.needs_manual_review is True


def test_routine_inquiry_gets_auto_reply():
    decision = decide(cls("opening_hours"))
    assert decision.risk is Risk.ROUTINE
    assert decision.actions == (Action.AUTO_REPLY,)
    assert decision.language == "de"
    assert decision.needs_manual_review is False


@pytest.mark.parametrize("category", sorted(ALWAYS_SENSITIVE))
def test_always_sensitive_categories_override_model_risk(category):
    decision = decide(cls(category, risk="routine"))
    assert decision.risk is Risk.SENSITIVE
    assert decision.actions == (Action.ACKNOWLEDGMENT, Action.DRAFT)


def test_model_can_mark_routine_category_sensitive():
    decision = decide(cls("new_patient", risk="sensitive"))
    assert decision.actions == (Action.ACKNOWLEDGMENT, Action.DRAFT)


def test_pain_is_an_emergency():
    assert decide(cls("pain_emergency", risk="sensitive")).emergency is True
    assert decide(cls("pricing", risk="sensitive")).emergency is False


def test_unsupported_language_gets_combined_acknowledgment_only():
    decision = decide(cls("appointment", language="tr"))
    assert decision.risk is Risk.SENSITIVE
    assert decision.actions == (Action.ACKNOWLEDGMENT,)
    assert decision.language == "de+en"
    assert decision.language_unsupported is True


@pytest.mark.parametrize("code, expected", [("DE", "de"), ("en-GB", "en"), ("de_DE", "de"), (" en ", "en")])
def test_language_codes_are_normalized(code, expected):
    assert normalize_language(code) == expected
    assert decide(cls("opening_hours", language=code)).language_unsupported is False


def test_outside_hours_mode_queues_routine_while_open():
    decision = decide(cls("opening_hours"), auto_reply_mode="outside_hours", practice_open=True)
    assert decision.actions == (Action.DRAFT,)
    assert decision.risk is Risk.ROUTINE


def test_outside_hours_mode_auto_replies_while_closed():
    decision = decide(cls("opening_hours"), auto_reply_mode="outside_hours", practice_open=False)
    assert decision.actions == (Action.AUTO_REPLY,)


def test_invalid_mode_is_rejected():
    with pytest.raises(ValueError):
        decide(cls("opening_hours"), auto_reply_mode="sometimes")


def test_downgrade_failed_auto_reply():
    decision = downgrade_failed_auto_reply(decide(cls("opening_hours", language="en")))
    assert decision.risk is Risk.SENSITIVE
    assert decision.actions == (Action.ACKNOWLEDGMENT, Action.DRAFT)
    assert decision.needs_manual_review is True
    assert decision.language == "en"
