"""End to end (A1 layer 4b): a form post through the real app to a real mail in Mailpit.

What is real: the FastAPI app (create_app), IntakeService, ReplyService, the pipeline (layers 1-3),
the test database and SmtpSender talking SMTP to Mailpit (localhost:1025). What is fake: the LLM
(shared.testing.FakeLLM with a script per test), so no test spends API money.
Mailpit's HTTP API (localhost:8025) is used to clear the inbox first and to read what arrived.
The tests skip, with the fix, when Mailpit is not running.
"""
import smtplib
import time
from datetime import UTC, datetime
from functools import partial

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lead_responder import config
from lead_responder.api.app import create_app
from lead_responder.db.models import Lead, Reply
from lead_responder.db.session import make_session_factory
from lead_responder.models import Category, Classification, Risk, Urgency
from lead_responder.pipeline import process_inquiry
from lead_responder.router import Action
from lead_responder.sender import SmtpSender
from lead_responder.services.intake import IntakeService
from lead_responder.services.replies import ReplyService
from shared.llm import LLMError
from shared.testing import FakeLLM

# 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 first and each connection then
# waits about a second before falling back, which made these tests several times slower.
MAILPIT_API = "http://127.0.0.1:8025/api/v1"
SMTP_HOST, SMTP_PORT = "127.0.0.1", 1025
EMAIL = "anna.e2e@example.com"


def utcnow() -> datetime:
    return datetime.now(UTC)


def cls(category: Category, risk: Risk, language: str = "de") -> Classification:
    return Classification(category=category, risk=risk, urgency=Urgency.NORMAL, language=language, reason="test")


def lead_json(message: str, email: str = EMAIL) -> dict:
    return {"name": "Anna Test", "email": email, "phone": None, "message": message, "consent": True}


# Skips the test when Mailpit's API does not answer; otherwise empties the inbox so each test
# counts only its own mails.
@pytest.fixture
def mailpit():
    try:
        httpx.get(f"{MAILPIT_API}/messages", timeout=2).raise_for_status()
    except httpx.HTTPError as e:
        pytest.skip(
            f"Mailpit not reachable at {MAILPIT_API}: run 'docker compose up -d mailpit' in "
            f"track-a-dental/a1-lead-responder ({type(e).__name__})"
        )
    httpx.delete(f"{MAILPIT_API}/messages", timeout=5).raise_for_status()
    return MAILPIT_API


# The messages in Mailpit. Waits up to `wait` seconds for at least `expect` of them (SMTP has
# answered "accepted" by the time send() returns, but Mailpit stores the mail a moment later).
def inbox(expect: int = 0, wait: float = 3.0) -> list[dict]:
    deadline = time.monotonic() + wait
    while True:
        messages = httpx.get(f"{MAILPIT_API}/messages", timeout=5).json()["messages"]
        if len(messages) >= expect or time.monotonic() > deadline:
            return messages
        time.sleep(0.1)


def message_text(message: dict) -> str:
    return httpx.get(f"{MAILPIT_API}/message/{message['ID']}", timeout=5).json()["Text"]


def recipients(message: dict) -> list[str]:
    return [to["Address"] for to in message["To"]]


# The real wiring of api/main.py's build_app, pointed at the test database, Mailpit and a fake LLM.
# auto_reply_mode is fixed to "always" so the routine route does not depend on the .env or the clock.
# smtplib names our host in EHLO via socket.getfqdn(), which takes about a second per connection
# on some Windows machines; a fixed name keeps the suite under the pre-commit time limit.
@pytest.fixture
def make_app(engine, session, practice, mailpit, monkeypatch):
    monkeypatch.setattr(smtplib.socket, "getfqdn", lambda *_args: "localhost")
    built: list[IntakeService] = []

    def build(llm: FakeLLM) -> TestClient:
        factory = make_session_factory(engine)
        sender = SmtpSender(host=SMTP_HOST, port=SMTP_PORT, from_addr=practice.email,
                            reply_to=practice.email, timeout=5)
        replies = ReplyService(session_factory=factory, sender=sender, practice=practice, clock=utcnow)
        intake = IntakeService(
            session_factory=factory, replies=replies, llm=llm, practice=practice, faq="FAQ", clock=utcnow,
            pipeline=partial(process_inquiry, auto_reply_mode="always"),
        )
        built.append(intake)
        return TestClient(create_app(intake=intake), raise_server_exceptions=False)

    yield build
    for intake in built:
        intake.shutdown()


def replies_by_kind(session) -> dict[str, Reply]:
    session.expire_all()
    return {reply.kind: reply for reply in session.scalars(select(Reply)).all()}


def test_routine_lead_reaches_mailpit(make_app, session, practice):
    client = make_app(FakeLLM(parse=[cls(Category.OPENING_HOURS, Risk.ROUTINE)],
                              text=[f"Hallo {{name}}, buchen Sie hier: {practice.doctolib_url}"]))
    assert client.post("/leads", json=lead_json("Haben Sie samstags geöffnet?")).status_code == 202

    [mail] = inbox(expect=1)
    assert recipients(mail) == [EMAIL]
    assert mail["Subject"] == f"Ihre Anfrage an die {practice.name}"
    text = message_text(mail)
    assert "Hallo Anna Test," in text and practice.doctolib_url in text
    replies = replies_by_kind(session)
    assert list(replies) == [Action.AUTO_REPLY.value] and replies[Action.AUTO_REPLY.value].status == "sent"
    assert session.scalar(select(Lead.status)) == "auto_replied"


def test_sensitive_lead_gets_acknowledgment_in_mailpit_and_a_draft_row(make_app, session, practice):
    client = make_app(FakeLLM(parse=[cls(Category.PRICING, Risk.SENSITIVE)],
                              text=["NONE", "Hallo {name}, ein Implantat besprechen wir gern persönlich."]))
    assert client.post("/leads", json=lead_json("Was kostet ein Implantat?")).status_code == 202

    [mail] = inbox(expect=1)
    text = message_text(mail)
    assert "Hallo Anna Test," in text and "Implantat" not in text  # the fixed acknowledgment, not the draft
    replies = replies_by_kind(session)
    assert replies[Action.ACKNOWLEDGMENT.value].status == "sent"
    assert replies[Action.DRAFT.value].status == "drafted"  # waits for staff approval
    assert session.scalar(select(Lead.status)) == "awaiting_approval"
    assert len(inbox(expect=2, wait=0.5)) == 1  # the draft was not mailed


def test_turkish_language_lead_gets_combined_acknowledgment_and_no_draft(make_app, session, practice):
    client = make_app(FakeLLM(parse=[cls(Category.APPOINTMENT, Risk.ROUTINE, language="tr")]))
    assert client.post("/leads", json=lead_json("Merhaba, randevu almak istiyorum.")).status_code == 202

    [mail] = inbox(expect=1)
    assert mail["Subject"] == f"Ihre Anfrage / Your inquiry: {practice.name}"
    text = message_text(mail)
    assert "Hallo Anna Test," in text and "Hello Anna Test," in text
    assert list(replies_by_kind(session)) == [Action.ACKNOWLEDGMENT.value]
    assert session.scalar(select(Lead.status)) == "acknowledged"


def test_duplicate_post_sends_nothing_more(make_app, session):
    client = make_app(FakeLLM(parse=[LLMError("down"), LLMError("down")]))
    body = lead_json("Ich möchte einen Termin.")
    assert client.post("/leads", json=body).status_code == 202
    assert len(inbox(expect=1)) == 1
    assert client.post("/leads", json=body).status_code == 202

    assert len(inbox(expect=2, wait=0.5)) == 1
    session.expire_all()
    assert len(session.scalars(select(Lead)).all()) == 1


# Hostile posts: a 500 KB body (413 before parsing), a whitespace-only message (422), and one
# mailbox under many +tag and Gmail-dot aliases (all 202, but at most MAX_LEADS_PER_EMAIL_HOUR mails).
def test_hostile_form_posts(make_app, session):
    client = make_app(FakeLLM(parse=[LLMError("down")] * 10))

    huge = client.post("/leads", content=b'{"message": "' + b"a" * 500_000 + b'"}',
                       headers={"content-type": "application/json"})
    assert huge.status_code == 413
    blank = client.post("/leads", json=lead_json("   \n\t  "))
    assert blank.status_code == 422

    aliases = [f"victim+{n}@example.com" for n in range(4)] + ["v.i.c.t.i.m@gmail.com", "victim+x@gmail.com",
                                                               "Vic.tim@googlemail.com"]
    for n, address in enumerate(aliases):
        response = client.post("/leads", json=lead_json(f"Frage Nummer {n}", email=address))
        assert response.status_code == 202, (address, response.status_code)

    mails = inbox(expect=10, wait=1.0)
    by_mailbox = {"example.com": 0, "gmail.com": 0}
    for mail in mails:
        by_mailbox[recipients(mail)[0].rsplit("@", 1)[1].replace("googlemail.com", "gmail.com")] += 1
    assert 1 <= by_mailbox["example.com"] <= config.MAX_LEADS_PER_EMAIL_HOUR
    assert 1 <= by_mailbox["gmail.com"] <= config.MAX_LEADS_PER_EMAIL_HOUR
    session.expire_all()
    assert all(lead.message.strip() for lead in session.scalars(select(Lead)).all())
