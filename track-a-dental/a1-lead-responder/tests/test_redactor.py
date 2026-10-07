import pytest

from lead_responder.redactor import redact


def test_redacts_email():
    assert redact("Schreiben Sie an anna.mueller@example.de bitte") == "Schreiben Sie an [EMAIL] bitte"


@pytest.mark.parametrize("phone", ["+49 171 1234567", "0171/1234567", "0511 - 123 45 67", "0049 511 1234567"])
def test_redacts_phone_numbers(phone):
    assert redact(f"Rufen Sie mich an: {phone}.") == "Rufen Sie mich an: [PHONE]."


def test_keeps_dates_and_times():
    text = "Hätten Sie am 12.10.2026 um 10:30 einen Termin?"
    assert redact(text) == text


def test_keeps_short_numbers():
    text = "Mein Sohn ist 8 und hat Zahn 36 abgebrochen."
    assert redact(text) == text


def test_redacts_multiple_occurrences():
    text = "Mail: a@b.de oder c@d.com, Tel 0170 0000123"
    assert redact(text) == "Mail: [EMAIL] oder [EMAIL], Tel [PHONE]"
