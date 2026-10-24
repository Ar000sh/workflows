"""Log hygiene (A1 layer 4b, spec section 8 and Global Constraints: "logs carry ids and event names only").

The rule under test: whatever happens to a lead, no log record holds its email, name, phone or
message, not in the message, not in its arguments and not in a logged exception's text. A send
error stored on a reply is the exception class name only, never the recipient address.

Each lead carries canary strings in every personal field, then goes through the whole flow with
the log level at DEBUG: the real app and IntakeService, the real pipeline (layers 1-3) driven by
a fake LLM, and a fake or real-but-patched SMTP sender. No real Claude API call, no real mail.
"""
import logging
import smtplib
import threading
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lead_responder import config
from lead_responder import sender as sender_module
from lead_responder.api.app import create_app
from lead_responder.api.schemas import LeadIn
from lead_responder.db.models import Lead, Reply
from lead_responder.db.session import make_session_factory
from lead_responder.models import Category, Classification, Risk, Urgency
from lead_responder.pipeline import process_inquiry
from lead_responder.sender import Outcome, SendResult, SmtpSender
from lead_responder.services.intake import IntakeService
from lead_responder.services.replies import ReplyService
from shared.llm import LLMError
from shared.testing import FakeLLM

# Canary fragments: if any of them shows up in a log record, personal data leaked.
NAME_CANARY = "Zqxname"
EMAIL_CANARY = "zqxmail"
PHONE_CANARY = "7771234"
MESSAGE_CANARY = "ZQXMSG"
CANARIES = (NAME_CANARY, EMAIL_CANARY, PHONE_CANARY, MESSAGE_CANARY)


def utcnow() -> datetime:
    return datetime.now(UTC)


# One form post whose every personal field carries a canary. `tag` keeps the mailboxes apart, so
# the per-email limit only hits where a test wants it to.
def lead_json(tag: str, message: str = "Was kostet eine Kontrolle?") -> dict:
    return {
        "name": f"{NAME_CANARY} Canaria", "email": f"{EMAIL_CANARY}{tag}@example.com",
        "phone": f"+49 170 {PHONE_CANARY}", "message": f"{message} Ref {MESSAGE_CANARY}-{tag}",
        "consent": True,
    }


def cls(category: Category, risk: Risk) -> Classification:
    return Classification(category=category, risk=risk, urgency=Urgency.NORMAL, language="de", reason="test")


# The LLM the IntakeService holds; each scenario loads a fresh script into it.
class SwitchableLLM:
    def __init__(self):
        self.current = FakeLLM()

    def script(self, *, parse=(), text=()):
        self.current = FakeLLM(parse=list(parse), text=list(text))

    def parse(self, **kwargs):
        return self.current.parse(**kwargs)

    def text(self, **kwargs):
        return self.current.text(**kwargs)


# A sender that accepts, or (fail=True) raises an exception whose text quotes the recipient and the
# body, as a careless SMTP library might. ReplyService must log the class name only.
class SwitchableSender:
    def __init__(self):
        self.fail = False
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def send(self, *, to: str, subject: str, body: str) -> SendResult:
        with self._lock:
            self.calls.append(to)
        if self.fail:
            raise RuntimeError(f"mailbox {to} rejected message: {body[:200]}")
        return SendResult(Outcome.ACCEPTED, "")


# Every text a log record can carry: the formatted message, the raw arguments, the exception text.
def record_texts(record: logging.LogRecord) -> list[str]:
    texts = [str(record.msg), repr(record.args)]
    try:
        texts.append(record.getMessage())
    except Exception:
        pass
    if record.exc_info:
        texts.append(logging.Formatter().formatException(record.exc_info))
    if record.exc_text:
        texts.append(record.exc_text)
    return texts


def leaks(records: list[logging.LogRecord]) -> list[str]:
    found = []
    for record in records:
        for text in record_texts(record):
            for canary in CANARIES:
                if canary.lower() in text.lower():
                    found.append(f"{record.name}: {canary} in {text[:160]!r}")
    return found


@pytest.fixture
def env(engine, session, practice):
    factory = make_session_factory(engine)
    llm, sender = SwitchableLLM(), SwitchableSender()
    replies = ReplyService(session_factory=factory, sender=sender, practice=practice, clock=utcnow)
    intake = IntakeService(
        session_factory=factory, replies=replies, llm=llm, practice=practice, faq="FAQ", clock=utcnow,
        pipeline=partial(process_inquiry, auto_reply_mode="always"),
    )
    client = TestClient(create_app(intake=intake), raise_server_exceptions=False)
    yield {"factory": factory, "llm": llm, "sender": sender, "intake": intake, "client": client}
    intake.shutdown()


def test_no_personal_data_in_logs(env, session, practice, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    client, llm, sender = env["client"], env["llm"], env["sender"]
    monkeypatch.setattr(config, "MAX_LEADS_PER_EMAIL_HOUR", 1)

    # 1. A normal routine lead: classified, auto-reply written by the AI, sent.
    llm.script(parse=[cls(Category.OPENING_HOURS, Risk.ROUTINE)],
               text=[f"Hallo {{name}}, buchen Sie hier: {practice.doctolib_url}"])
    assert client.post("/leads", json=lead_json("normal", "Haben Sie samstags offen?")).status_code == 202

    # 2. A failing LLM whose error text quotes the input (an API error can repeat what it was sent).
    llm.script(parse=[LLMError(f"non-retryable API error 400: input {MESSAGE_CANARY} {EMAIL_CANARY}x@example.com")])
    assert client.post("/leads", json=lead_json("llmfail")).status_code == 202

    # 3. A failing sender whose exception quotes the address and the body.
    llm.script(parse=[LLMError("down")])
    sender.fail = True
    assert client.post("/leads", json=lead_json("sendfail")).status_code == 202
    sender.fail = False

    # 4. A guardrail rejection: the AI's safe-info paragraph echoes a link from the message, so the
    #    violation text ("link that is not the booking link: ...") would quote message content.
    llm.script(parse=[cls(Category.PRICING, Risk.SENSITIVE)],
               text=[f"Mehr unter https://{MESSAGE_CANARY.lower()}.example/info", "Hallo {name}, wir melden uns."])
    assert client.post("/leads", json=lead_json("guard", "Was kostet ein Implantat?")).status_code == 202

    # 5. Over the per-email limit (set to 1 above): the second lead of this mailbox is suppressed.
    llm.script(parse=[LLMError("down")])
    assert client.post("/leads", json=lead_json("over")).status_code == 202
    assert client.post("/leads", json=lead_json("over", "Noch eine Frage.")).status_code == 202

    # 6. A duplicate post.
    llm.script(parse=[LLMError("down")])
    assert client.post("/leads", json=lead_json("dup")).status_code == 202
    assert client.post("/leads", json=lead_json("dup")).status_code == 202

    # 7. A sweep run, an hour later: a lead whose processing never ran gets the fallback, and the
    #    in-doubt acknowledgment from step 3 is resent.
    assert env["intake"].receive(LeadIn(**lead_json("sweep"))) is not None
    later = ReplyService(
        session_factory=env["factory"], sender=sender, practice=practice,
        clock=lambda: utcnow() + timedelta(hours=1),
    )
    result = later.sweep()

    # The flow really went where the scenarios meant it to.
    assert result.fallback_acknowledgments == 1 and result.retried_acknowledgments == 1
    session.expire_all()
    assert len(session.scalars(select(Lead)).all()) == 8  # the duplicate was not stored
    assert any(r.status == "sent" for r in session.scalars(select(Reply)).all())

    assert caplog.records, "nothing was logged: the test would prove nothing"
    assert leaks(caplog.records) == []


# A refusing SMTP server repeats the recipient address in its error (SMTPRecipientsRefused holds
# a dict keyed by the address). The real SmtpSender stores and logs the class name only.
def test_send_error_text_holds_no_address(engine, session, practice, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    address = f"{EMAIL_CANARY}refused@example.com"

    class RefusingSMTP:
        def __init__(self, host, port, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def send_message(self, msg):
            raise smtplib.SMTPRecipientsRefused({address: (550, f"5.1.1 <{address}> unknown".encode())})

    monkeypatch.setattr(sender_module.smtplib, "SMTP", RefusingSMTP)
    smtp = SmtpSender(host="localhost", port=1025, from_addr="praxis@example.com",
                      reply_to="praxis@example.com", sleep=lambda _s: None)
    factory = make_session_factory(engine)
    replies = ReplyService(session_factory=factory, sender=smtp, practice=practice, clock=utcnow)
    intake = IntakeService(
        session_factory=factory, replies=replies, llm=FakeLLM(parse=[LLMError("down")]), practice=practice,
        faq="FAQ", clock=utcnow,
    )
    try:
        received = intake.receive(LeadIn(**{**lead_json("refused"), "email": address}))
        intake.process(received.lead_id)
    finally:
        intake.shutdown()

    session.expire_all()
    [reply] = session.scalars(select(Reply)).all()
    assert reply.status == "failed"
    assert reply.error == "SMTPRecipientsRefused"
    assert EMAIL_CANARY not in (reply.error or "")
    assert leaks(caplog.records) == []
