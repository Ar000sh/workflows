from lead_responder import drafter
from lead_responder.config import DRAFTER_EFFORT, DRAFTER_MODEL
from shared.testing import FakeLLM

FAQ = "Wir haben Montag bis Freitag geöffnet."


def only_call(llm):
    [call] = llm.text_calls
    return call


def test_auto_reply_uses_drafter_settings_and_rules(practice):
    llm = FakeLLM(text=["Hallo {name}, ..."])
    result = drafter.auto_reply(llm, redacted_message="Haben Sie samstags geöffnet?",
                                language="de", practice=practice, faq=FAQ)
    assert result.value == "Hallo {name}, ..."
    call = only_call(llm)
    assert call["model"] == DRAFTER_MODEL
    assert call["effort"] == DRAFTER_EFFORT
    assert call["fallbacks"] is True
    assert call["user"] == "<inquiry>\nHaben Sie samstags geöffnet?\n</inquiry>"
    assert FAQ in call["system"]
    assert practice.doctolib_url in call["system"]
    assert "German" in call["system"]
    assert 'First line exactly: "Hallo {name},"' in call["system"]
    assert "Never state prices" in call["system"]


def test_english_reply_uses_english_greeting(practice):
    llm = FakeLLM(text=["Hello {name}, ..."])
    drafter.auto_reply(llm, redacted_message="Open on Saturday?", language="en", practice=practice, faq=FAQ)
    system = only_call(llm)["system"]
    assert 'First line exactly: "Hello {name},"' in system
    assert "English" in system


def test_safe_info_allows_nothing_to_add(practice):
    llm = FakeLLM(text=[drafter.NO_SAFE_INFO])
    result = drafter.safe_info(llm, redacted_message="Was kostet ein Implantat?",
                               language="de", practice=practice, faq=FAQ)
    assert result.value == "NONE"
    system = only_call(llm)["system"]
    assert "exactly: NONE" in system
    assert "First line exactly" not in system  # a paragraph, not an email


def test_staff_draft_mentions_emergency_only_when_needed(practice):
    llm = FakeLLM(text=["a", "b"])
    drafter.staff_draft(llm, redacted_message="Zahnschmerzen", language="de", practice=practice, faq=FAQ, emergency=True)
    drafter.staff_draft(llm, redacted_message="Rechnung", language="de", practice=practice, faq=FAQ, emergency=False)
    emergency_call, normal_call = llm.text_calls
    assert "112" in emergency_call["system"]
    assert "112" not in normal_call["system"]


def test_load_faq_reads_practice_file():
    assert "BrightSmile" in drafter.load_faq()


def test_fill_name():
    assert drafter.fill_name("Hallo {name}, danke.", "Anna") == "Hallo Anna, danke."


def test_fill_name_without_name():
    assert drafter.fill_name("Hallo {name}, danke.", " ") == "Hallo, danke."


def test_fill_name_with_braces_is_literal():
    assert drafter.fill_name("Hallo {name},", "{x}") == "Hallo {x},"
