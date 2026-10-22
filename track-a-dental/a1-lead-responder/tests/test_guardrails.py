import pytest

from lead_responder.guardrails import check


def ok_text(practice):
    return f"Hallo {{name}}, Sie können online buchen: {practice.doctolib_url}. Bis bald!"


def test_clean_text_passes(practice):
    assert check(ok_text(practice), practice, require_doctolib=True) == []


def test_missing_doctolib_link(practice):
    assert check("Hallo, bis bald!", practice, require_doctolib=True) == ["missing Doctolib link"]


def test_doctolib_not_required(practice):
    assert check("Hallo, bis bald!", practice, require_doctolib=False) == []


@pytest.mark.parametrize("price", ["150 €", "ca. 80 Euro", "€ 99", "EUR 20", "1.200,50€", "ab 80,- €", "80,- Euro", "EUR80", "Eur. 80", "80 Euros"])
def test_prices_are_flagged(practice, price):
    violations = check(f"Das kostet {price}.", practice, require_doctolib=False)
    assert violations == ["mentions a price"]


@pytest.mark.parametrize("text", ["Termin am 12.10. um 10 Uhr", "Europa", "um 10 Uhr", "https://www.doctolib.de/zahnarztpraxis/hannover/brightsmile-demo", "Zahn 36"])
def test_normal_text_is_not_a_price(practice, text):
    assert check(text, practice, require_doctolib=False) == []


def test_dates_are_not_prices(practice):
    assert check("Termin am 12.10. um 10 Uhr", practice, require_doctolib=False) == []


def test_diagnosis_wording_is_flagged_case_insensitive(practice):
    [violation] = check("Das ist vermutlich eine ENTZÜNDUNG.", practice, require_doctolib=False)
    assert violation.startswith("diagnosis-like wording")
    assert "entzündung" in violation
