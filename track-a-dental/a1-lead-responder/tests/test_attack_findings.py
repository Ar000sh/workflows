"""Regression tests for the findings of the independent attack on A1 (2026-10-23).

Each test names the finding it pins down. Findings: 1 name field, 2 auto-reply guardrails,
3 keyword net gaps, 4 non-DE/EN pain, 5 safe-info 'NONE' variants.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from lead_responder.guardrails import check
from lead_responder.models import Category, Classification, Risk, Urgency
from lead_responder.pipeline import process_inquiry
from lead_responder.prompting import sanitize_name
from lead_responder.router import Action, decide, mentions_urgent_signs
from shared.testing import FakeLLM

MONDAY_10 = datetime(2026, 10, 5, 10, 0, tzinfo=ZoneInfo("Europe/Berlin"))


def cls(category="opening_hours", risk="routine", language="de"):
    return Classification(category=Category(category), risk=Risk(risk), urgency=Urgency.NORMAL,
                          language=language, reason="test")


def run(practice, *, name="Anna", message="Haben Sie samstags geöffnet?", classification=None, text=None):
    llm = FakeLLM(parse=[classification or cls()],
                  text=text if text is not None else [f"Hallo {{name}}, buchen Sie hier: {practice.doctolib_url}"])
    return process_inquiry(llm=llm, practice=practice, faq="FAQ", name=name, message=message, now=MONDAY_10)


# ---- finding 1: the name field goes into replies nobody reviews ----

@pytest.mark.parametrize("name", ["Anna Müller", "Dr. Müller-Schmidt", "O'Brien", "José", "Anna Maria von der Heide"])
def test_normal_names_are_kept(name):
    assert sanitize_name(name) == name


@pytest.mark.parametrize("name", [None, 5, ["a"], b"bytes"])
def test_non_string_names_become_empty(name):
    assert sanitize_name(name) == ""


def test_control_characters_and_newlines_become_spaces():
    assert sanitize_name("Anna\n\nMüller\t") == "Anna Müller"
    assert sanitize_name("Anna\u200bMüller") == "Anna Müller"      # zero-width space


@pytest.mark.parametrize("name", [
    "Kunde http://evil.example/pay", "visit www.evil.example", "anna@example.com", "x://y",
    "<b>Anna</b>", "{name}", "Anna [Name]", "Anna | Bank",
])
def test_hostile_or_markup_names_are_dropped(name):
    assert sanitize_name(name) == ""


def test_name_is_capped():
    assert len(sanitize_name("A" * 1_000_000)) <= 60


def test_digits_and_punctuation_are_removed_from_names():
    assert sanitize_name("Anna 0900 123456!") == "Anna"


def test_phishing_name_cannot_put_a_link_into_the_reply(practice):
    evil = "Kunde,\n\nIhr Termin wurde storniert. Zahlen Sie unter http://evil.example/pay\n\nIhre Bank"
    result = run(practice, name=evil)
    for reply in result.replies:
        assert "evil.example" not in reply.body and "storniert" not in reply.body
        assert reply.body.startswith("Hallo,")


def test_huge_name_gives_a_short_reply(practice):
    result = run(practice, name="X" * 1_000_000)
    assert len(result.replies[0].body) < 1000


@pytest.mark.parametrize("name", [None, 5])
def test_non_string_name_does_not_crash_the_pipeline(practice, name):
    result = run(practice, name=name)
    assert result.replies[0].body.startswith("Hallo,")


# ---- finding 2: guardrails must check links, phones, placeholders and length ----

def test_foreign_links_are_flagged(practice):
    for text in ["Siehe http://evil.example/pay", "Siehe www.evil.example", f"{practice.doctolib_url}.evil.example/login"]:
        assert any("link" in v for v in check(text, practice, require_doctolib=False)), text


def test_booking_link_is_allowed_with_trailing_punctuation(practice):
    for text in [f"Buchen: {practice.doctolib_url}", f"Buchen ({practice.doctolib_url}).", f"{practice.doctolib_url}."]:
        assert check(text, practice, require_doctolib=True) == [], text


def test_lookalike_link_does_not_count_as_the_booking_link(practice):
    violations = check(f"Hallo {practice.doctolib_url}.evil.example/login", practice, require_doctolib=True)
    assert "missing Doctolib link" in violations


def test_foreign_phone_numbers_and_emails_are_flagged(practice):
    for text in ["Rufen Sie 0900 5 123456 an", "Schreiben Sie an kasse@evil.example", "Konto DE89 3704 0044 0532 0130 00"]:
        assert any("phone number, email or ID" in v for v in check(text, practice, require_doctolib=False)), text


def test_practice_phone_and_dates_are_allowed(practice):
    text = f"Rufen Sie uns an: {practice.phone}. Termin am 12.10.2026 um 10:30. Notdienst {practice.notdienst_phone}."
    assert check(text, practice, require_doctolib=False) == []


@pytest.mark.parametrize("text", ["Hallo [Name],", "Hallo {vorname},", "Hallo {name},", "Hallo [name]"])
def test_placeholders_are_flagged(practice, text):
    assert "contains a placeholder" in check(text, practice, require_doctolib=False)


def test_overlong_text_is_flagged_only_when_a_limit_is_given(practice):
    text = "wort " * 300
    assert check(text, practice, require_doctolib=False) == []
    assert any("too long" in v for v in check(text, practice, require_doctolib=False, max_words=200))


@pytest.mark.parametrize("ai_text", [
    "Hallo {name}, ruf 0900 5 123456 oder {url}",
    "Hallo {name}, {url} und http://evil.example",
    "Hallo [Name], {url}",
    "Hallo {name}, " + "wort " * 400 + "{url}",
])
def test_bad_auto_reply_is_not_sent(practice, ai_text):
    result = run(practice, text=[ai_text.replace("{url}", practice.doctolib_url), "NONE", "Hallo {name}, Entwurf."])
    assert Action.AUTO_REPLY not in [r.kind for r in result.replies]
    assert result.decision.needs_manual_review is True


# ---- finding 5: safe-info that says "nothing" in other words, or breaks the rules ----

@pytest.mark.parametrize("text", ["NONE - nothing relevant", "None of the facts apply.", "none", "Keine.", "Keine passenden Infos."])
def test_safe_info_that_means_nothing_is_dropped(practice, text):
    result = run(practice, classification=cls("pricing", "sensitive"), message="Was kostet ein Implantat?",
                 text=[text, "Hallo {name}."])
    assert result.replies[0].ai_body is None
    assert text not in result.replies[0].body


@pytest.mark.parametrize("text", [
    "Rufen Sie 0900 1234567 an.", "Siehe http://evil.example", "Nehmen Sie Ibuprofen " + "wort " * 120,
])
def test_unsafe_safe_info_is_rejected(practice, text):
    result = run(practice, classification=cls("pricing", "sensitive"), message="Was kostet ein Implantat?",
                 text=[text, "Hallo {name}."])
    assert result.replies[0].ai_body is None          # foreign number, foreign link, or too long: dropped


# ---- finding 3: keyword net gaps ----

@pytest.mark.parametrize("text", [
    "Zahnweh", "toothache since monday", "Zahnfleischbluten", "Zahn gebrochen", "Zahn gesplittert", "Zahn ist raus",
    "Eiter am Zahnfleisch", "pus near gum", "Weisheitszahn entzündet", "my gum is sore", "tooth is killing me",
    "throbbing tooth", "Füllung verloren", "kann nicht schlafen wegen Zahn", "Taubheitsgefühl", "ich bin schwanger",
    "gum infection", "Zahnfleisch brennt", "my tooth came out", "I'm pregnant",
])
def test_more_pain_phrases_trip_the_net(text):
    assert mentions_urgent_signs(text) is True


@pytest.mark.parametrize("text", [
    "Wie geht es weiter?", "Welche Sprache sprechen Sie?", "Do you speak English?", "Phone number please",
    "Staubsauger im Wartezimmer", "Number of parking spots?", "Ich möchte einen Termin zur Kontrolle.",
])
def test_calm_phrases_still_do_not_trip_the_net(text):
    assert mentions_urgent_signs(text) is False


# ---- finding 4: pain in a language we do not read ----

def test_unsupported_language_always_gets_emergency_info_and_a_staff_flag():
    decision = decide(cls("appointment", language="tr"))
    assert decision.language_unsupported is True
    assert decision.emergency is True
    assert decision.needs_manual_review is True


def test_turkish_pain_message_acknowledgment_carries_the_emergency_numbers(practice):
    result = run(practice, message="Dis agrisi var, randevu", classification=cls("appointment", "routine", "tr"), text=[])
    assert result.decision.needs_manual_review is True
    assert "112" in result.replies[0].body
