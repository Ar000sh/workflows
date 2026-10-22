import pytest

from lead_responder.redactor import redact


def test_redacts_email():
    assert redact("Schreiben Sie an anna.mueller@example.de bitte") == "Schreiben Sie an [EMAIL] bitte"


@pytest.mark.parametrize("phone", ["+49 171 1234567", "0171/1234567", "0511 - 123 45 67", "0049 511 1234567"])
def test_redacts_phone_numbers(phone):
    assert redact(f"Rufen Sie mich an: {phone}.") == "Rufen Sie mich an: [NUMBER]."


def test_keeps_dates_and_times():
    text = "Hätten Sie am 12.10.2026 um 10:30 einen Termin?"
    assert redact(text) == text


def test_keeps_short_numbers():
    text = "Mein Sohn ist 8 und hat Zahn 36 abgebrochen."
    assert redact(text) == text


def test_redacts_multiple_occurrences():
    text = "Mail: a@b.de oder c@d.com, Tel 0170 0000123"
    assert redact(text) == "Mail: [EMAIL] oder [EMAIL], Tel [NUMBER]"


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
    assert redact("Tel (0511) 123456") == "Tel [NUMBER]"


def test_redacts_dotted_phone_that_looks_like_a_date():
    assert redact("Tel 0511.12.34.56") == "Tel [NUMBER]"


@pytest.mark.parametrize("iban", [
    "DE89 3704 0044 0532 0130 00",
    "DE89370400440532013000",
    "de89 3704 0044 0532 0130 00",
    "AT61 1904 3002 3457 3201",
])
def test_redacts_iban(iban):
    assert redact(f"Meine IBAN: {iban} danke") == "Meine IBAN: [NUMBER] danke"


@pytest.mark.parametrize("insurance_id", ["A123456789", "z987654321"])
def test_redacts_insurance_number(insurance_id):
    assert redact(f"Versichertennummer {insurance_id}.") == "Versichertennummer [NUMBER]."


def test_redacts_card_number():
    assert redact("Karte 4111 1111 1111 1111") == "Karte [NUMBER]"


def test_keeps_plz_and_tooth_notation():
    text = "Praxis 30159 Hannover, Zahn 36, Termin Zimmer 12 - 14 Uhr"
    assert redact(text) == text


def test_keeps_words_around_iban():
    assert redact("IBAN DE89 3704 0044 0532 0130 00 und Tel 0511 1234567") == "IBAN [NUMBER] und Tel [NUMBER]"


def test_date_next_to_insurance_number_survives():
    assert redact("Am 12.10.2026 A123456789") == "Am 12.10.2026 [NUMBER]"


@pytest.mark.parametrize("date_text", [
    "Termin am 12/10/2026 14:30",
    "Termin am 12-10-2026.",
    "Appointment on 2026-10-12 at 10:30",
    "from 1/9/2026 to 30/9/2026",
    "am 12/10/2026 14 Uhr",
])
def test_keeps_slash_dash_and_iso_dates(date_text):
    assert redact(date_text) == date_text


@pytest.mark.parametrize("phone", ["0511-123-4567", "0511-1234567", "+49-511-1234567", "0511/123-456-7"])
def test_dashed_phones_are_still_redacted(phone):
    assert redact(f"Tel {phone}") == "Tel [NUMBER]"
