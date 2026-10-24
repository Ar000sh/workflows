import pytest
from pydantic import ValidationError

from lead_responder.api.schemas import LeadIn


def make(**overrides) -> dict:
    fields = dict(name="Anna", email="anna@example.com", phone=None, message="Termin?", consent=True)
    fields.update(overrides)
    return fields


def test_valid_lead_is_accepted():
    lead = LeadIn(**make())
    assert lead.email == "anna@example.com" and lead.phone is None


def test_consent_missing_or_false_is_rejected():
    fields = make()
    del fields["consent"]
    with pytest.raises(ValidationError):
        LeadIn(**fields)
    with pytest.raises(ValidationError):
        LeadIn(**make(consent=False))


@pytest.mark.parametrize("email", ["not-an-email", "a@b.de\nBcc: x@y.de"])
def test_bad_email_is_rejected(email):
    with pytest.raises(ValidationError):
        LeadIn(**make(email=email))


def test_255_character_email_is_rejected():
    email = "a" * 63 + "@" + "b" * 63 + "." + "c" * 63 + "." + "d" * 60 + ".de"
    assert len(email) == 255
    with pytest.raises(ValidationError):
        LeadIn(**make(email=email))


def test_overlong_name_message_phone_are_rejected():
    with pytest.raises(ValidationError):
        LeadIn(**make(name="x" * 201))
    with pytest.raises(ValidationError):
        LeadIn(**make(message="x" * 10_001))
    with pytest.raises(ValidationError):
        LeadIn(**make(phone="1" * 51))


def test_limits_themselves_are_accepted():
    lead = LeadIn(**make(name="x" * 200, message="ä😀" * 5000, phone="1" * 50))
    assert len(lead.name) == 200 and len(lead.message) == 10_000


@pytest.mark.parametrize("field", ["name", "message"])
def test_whitespace_only_is_rejected(field):
    with pytest.raises(ValidationError):
        LeadIn(**make(**{field: "  \n\t "}))


def test_nul_is_stripped_and_text_trimmed():
    lead = LeadIn(**make(message="  a\x00b  ", name="\x00 Anna "))
    assert lead.message == "ab" and lead.name == "Anna"


def test_lone_surrogate_is_rejected():
    with pytest.raises(ValidationError):
        LeadIn(**make(message="hi \ud800"))


def test_emoji_and_umlauts_are_accepted():
    lead = LeadIn(**make(name="Jörg Müller", message="Zahnschmerzen 😀"))
    assert lead.message == "Zahnschmerzen 😀"
