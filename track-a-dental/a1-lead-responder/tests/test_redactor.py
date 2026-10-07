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


@pytest.mark.parametrize("date_text", [
    "Termin am 12.10.2026 10:30 Uhr",
    "Vom 12.10.2026 - 14.10.2026 bin ich weg",
    "am 12. 10. 2026 um 10:30",
    "Rechnung 2026.10.12",
])
def test_keeps_realistic_dates(date_text):
    """Realistic date formats in messages must survive redaction."""
    assert redact(date_text) == date_text


def test_redacts_phone_with_leading_paren():
    """Leading paren of area code should be consumed, no unbalanced bracket."""
    assert redact("Tel (0511) 123456") == "Tel [PHONE]"
