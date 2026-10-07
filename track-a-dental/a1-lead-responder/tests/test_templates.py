import pytest

from lead_responder.templates import acknowledgment


def ack(practice, language="de", **kwargs):
    kwargs.setdefault("name", "Anna")
    kwargs.setdefault("emergency", False)
    kwargs.setdefault("practice_open", True)
    return acknowledgment(language, practice=practice, **kwargs)


def test_german_standard(practice):
    text = ack(practice)
    assert text.startswith("Hallo Anna,")
    assert "Zahnarztpraxis BrightSmile" in text
    assert "112" not in text


def test_emergency_while_open_gives_practice_phone(practice):
    text = ack(practice, emergency=True, practice_open=True)
    assert practice.phone in text
    assert "112" in text
    assert practice.notdienst_phone not in text


def test_emergency_while_closed_gives_notdienst(practice):
    text = ack(practice, emergency=True, practice_open=False)
    assert practice.notdienst_phone in text
    assert "112" in text


def test_english(practice):
    text = ack(practice, language="en")
    assert text.startswith("Hello Anna,")
    assert "Kind regards" in text


def test_combined_contains_both_languages(practice):
    text = ack(practice, language="de+en", safe_info="ignored")
    assert "Hallo Anna," in text and "Hello Anna," in text
    assert "ignored" not in text


def test_safe_info_is_included(practice):
    text = ack(practice, safe_info="Gesetzliche Kassen übernehmen zweimal im Jahr die Vorsorge.")
    assert "zweimal im Jahr" in text


def test_missing_name(practice):
    assert ack(practice, name="  ").startswith("Hallo,")


def test_name_with_braces_is_literal(practice):
    assert ack(practice, name="{evil}").startswith("Hallo {evil},")


def test_unknown_language_rejected(practice):
    with pytest.raises(ValueError):
        ack(practice, language="tr")
