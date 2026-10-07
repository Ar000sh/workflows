from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from lead_responder.models import Category, Classification, Risk, Urgency
from lead_responder.pipeline import (
    EVENT_AUTO_REPLY_FAILED, EVENT_CLASSIFIER_FAILED, EVENT_MESSAGE_TOO_LONG, EVENT_SAFE_INFO_DROPPED,
    process_inquiry,
)
from lead_responder.router import Action
from shared.llm import LLMError
from shared.testing import FakeLLM

BERLIN = ZoneInfo("Europe/Berlin")
MONDAY_10 = datetime(2026, 10, 5, 10, 0, tzinfo=BERLIN)   # practice open
SUNDAY_10 = datetime(2026, 10, 4, 10, 0, tzinfo=BERLIN)   # practice closed


def cls(category, risk="routine", language="de"):
    return Classification(category=Category(category), risk=Risk(risk), urgency=Urgency.NORMAL,
                          language=language, reason="test")


def run(llm, practice, *, message="Haben Sie samstags geöffnet?", name="Anna", now=MONDAY_10, mode="always"):
    return process_inquiry(llm=llm, practice=practice, faq="FAQ", name=name, message=message,
                           now=now, auto_reply_mode=mode)


def kinds(result):
    return [reply.kind for reply in result.replies]


def test_routine_inquiry_gets_auto_reply(practice):
    llm = FakeLLM(parse=[cls("opening_hours")], text=[f"Hallo {{name}}, buchen Sie hier: {practice.doctolib_url}"])
    result = run(llm, practice)
    assert kinds(result) == [Action.AUTO_REPLY]
    assert result.replies[0].body.startswith("Hallo Anna,")
    assert result.replies[0].ai_body.startswith("Hallo {name},")
    assert result.decision.needs_manual_review is False
    assert result.cost_usd == pytest.approx(0.002)


def test_auto_reply_breaking_guardrail_becomes_draft(practice):
    bad = "Hallo {name}, eine Reinigung kostet 80 €."
    llm = FakeLLM(parse=[cls("opening_hours")], text=[bad, "NONE"])
    result = run(llm, practice)
    assert kinds(result) == [Action.ACKNOWLEDGMENT, Action.DRAFT]
    assert result.decision.risk is Risk.SENSITIVE
    assert result.decision.needs_manual_review is True
    draft = result.replies[1]
    assert "missing Doctolib link" in draft.warnings and "mentions a price" in draft.warnings
    assert any(e.startswith(EVENT_AUTO_REPLY_FAILED) for e in result.events)


def test_auto_reply_api_failure_falls_back_to_staff(practice):
    llm = FakeLLM(parse=[cls("opening_hours")], text=[LLMError("down"), "NONE", "Hallo {name}, Entwurf."])
    result = run(llm, practice)
    assert kinds(result) == [Action.ACKNOWLEDGMENT, Action.DRAFT]
    assert result.decision.needs_manual_review is True


def test_sensitive_pricing_gets_ack_with_safe_info_and_draft(practice):
    safe = "Für größere Behandlungen erstellen wir vorab einen persönlichen Heil- und Kostenplan."
    llm = FakeLLM(parse=[cls("pricing", "sensitive")], text=[safe, "Hallo {name}, wir melden uns."])
    result = run(llm, practice, message="Was kostet ein Implantat?")
    assert kinds(result) == [Action.ACKNOWLEDGMENT, Action.DRAFT]
    ack, draft = result.replies
    assert safe in ack.body and ack.ai_body == safe
    assert draft.body == "Hallo Anna, wir melden uns."


def test_safe_info_breaking_guardrail_is_dropped(practice):
    llm = FakeLLM(parse=[cls("pricing", "sensitive")], text=["Ein Implantat kostet ca. 2000 €.", "Hallo {name}."])
    result = run(llm, practice, message="Was kostet ein Implantat?")
    ack = result.replies[0]
    assert "2000" not in ack.body and ack.ai_body is None
    assert any(e.startswith(EVENT_SAFE_INFO_DROPPED) for e in result.events)


def test_pain_while_closed_gives_notdienst(practice):
    llm = FakeLLM(parse=[cls("pain_emergency", "sensitive")],
                  text=["NONE", "Hallo {name}, bitte rufen Sie die 112 an, wenn ..."])
    result = run(llm, practice, message="Starke Zahnschmerzen!", now=SUNDAY_10)
    ack, draft = result.replies
    assert practice.notdienst_phone in ack.body and "112" in ack.body
    assert draft.warnings == ()


def test_classifier_failure_means_manual_review_without_ai_text(practice):
    llm = FakeLLM(parse=[LLMError("down")])
    result = run(llm, practice)
    assert kinds(result) == [Action.ACKNOWLEDGMENT]
    assert result.decision.needs_manual_review is True
    assert "Hallo Anna," in result.replies[0].body and "Hello Anna," in result.replies[0].body
    assert "112" in result.replies[0].body
    assert llm.text_calls == []
    assert any(e.startswith(EVENT_CLASSIFIER_FAILED) for e in result.events)


def test_unsupported_language_gets_combined_ack_only(practice):
    llm = FakeLLM(parse=[cls("appointment", language="tr")])
    result = run(llm, practice, message="Merhaba, randevu almak istiyorum.")
    assert kinds(result) == [Action.ACKNOWLEDGMENT]
    assert result.decision.language_unsupported is True
    assert llm.text_calls == []


def test_contact_details_never_reach_the_ai(practice):
    llm = FakeLLM(parse=[cls("pricing", "sensitive")], text=["NONE", "Hallo {name}."])
    run(llm, practice, name="Anna Müller", message="Preis? Mail: anna@example.de, Tel 0170 0000123")
    sent = [c["user"] for c in llm.parse_calls + llm.text_calls]
    for text in sent:
        assert "anna@example.de" not in text and "0170 0000123" not in text and "Anna" not in text
        assert "[EMAIL]" in text and "[PHONE]" in text


def test_empty_message_is_rejected(practice):
    with pytest.raises(ValueError):
        run(FakeLLM(), practice, message="   ")


def test_very_long_message_skips_ai(practice):
    llm = FakeLLM()
    result = run(llm, practice, message="x" * 5001)
    assert llm.parse_calls == [] and llm.text_calls == []
    assert result.decision.needs_manual_review is True
    assert any(e.startswith(EVENT_MESSAGE_TOO_LONG) for e in result.events)


def test_outside_hours_mode_drafts_routine_while_open(practice):
    llm = FakeLLM(parse=[cls("opening_hours")], text=["Hallo {name}, Entwurf."])
    result = run(llm, practice, mode="outside_hours", now=MONDAY_10)
    assert kinds(result) == [Action.DRAFT]
