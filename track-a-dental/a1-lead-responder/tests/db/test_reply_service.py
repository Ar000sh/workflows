"""ReplyService.send and acknowledge_with_fallback (A1 layer 4b, spec sections 6 "Sending" and 7).

The rule under test: an email goes out only after a committed claim (reply -> "sending"), exactly
one of two racing senders wins, no database transaction is open while the sender runs, and the
result is recorded as "sent", "failed" (class name only) or left "sending" when unknown.
A fake Sender stands in for SMTP; it records every call and what the database looked like then.
"""
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from lead_responder import templates
from lead_responder.db.models import LeadStatus, ReplyStatus
from lead_responder.limits import mailbox_key
from lead_responder.repository import Repository
from lead_responder.router import Action
from lead_responder.sender import Outcome, SendResult
from lead_responder.services.replies import ReplyService, SendOutcome

NOW = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)
EMAIL = "anna.patient@example.com"


# Hands out one new Session per call (like sessionmaker) and remembers each one, so the fake
# sender can check that none of them is inside a transaction while it runs.
class RecordingSessionFactory:
    def __init__(self, engine):
        self.engine = engine
        self.sessions: list[Session] = []
        self._lock = threading.Lock()

    def __call__(self) -> Session:
        s = Session(self.engine, expire_on_commit=False)
        with self._lock:
            self.sessions.append(s)
        return s


# Records each send and returns the scripted outcome. While "sending" it also notes whether any
# service session was in a transaction and how many database connections sat "idle in
# transaction" (a lock or snapshot held across the send would show up there). `delay` keeps the
# send in flight for a moment so two racing threads overlap.
class FakeSender:
    def __init__(self, engine, factory, outcome=Outcome.ACCEPTED, detail="", delay=0.0):
        self.engine, self.factory = engine, factory
        self.outcome, self.detail, self.delay = outcome, detail, delay
        self.calls: list[dict] = []
        self.open_transactions: list[bool] = []
        self.idle_in_transaction: list[int] = []
        self._lock = threading.Lock()

    def send(self, *, to: str, subject: str, body: str) -> SendResult:
        with self._lock:
            self.calls.append({"to": to, "subject": subject, "body": body})
            self.open_transactions.append(any(s.in_transaction() for s in self.factory.sessions))
        with self.engine.connect() as conn:
            n = conn.scalar(text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND state LIKE 'idle in transaction%' AND pid <> pg_backend_pid()"
            ))
        self.idle_in_transaction.append(n)
        time.sleep(self.delay)
        return SendResult(self.outcome, self.detail)


@pytest.fixture
def factory(engine, session):  # `session` truncates the tables first
    f = RecordingSessionFactory(engine)
    yield f
    for s in f.sessions:
        s.close()


def make_service(factory, practice, sender) -> ReplyService:
    return ReplyService(session_factory=factory, sender=sender, practice=practice, clock=lambda: NOW)


def make_lead(repo: Repository, email=EMAIL):
    lead, _ = repo.create_lead_if_new(
        name="Anna Test", email=email, phone=None, message="Ich habe Zahnschmerzen.",
        consent_at=NOW, now=NOW, mailbox_key=mailbox_key(email),
    )
    return lead


def make_ack(repo: Repository, kind=Action.ACKNOWLEDGMENT, email=EMAIL):
    lead = make_lead(repo, email)
    if kind is Action.AUTO_REPLY:
        repo.set_lead_status(lead.id, LeadStatus.CLASSIFIED)
    reply = repo.acknowledge_lead(lead.id, kind=kind, ai_body=None, body="Danke, wir melden uns.", now=NOW)
    return lead, reply


def test_sent_reply_is_recorded_sent_with_sent_at(session, factory, practice, engine):
    repo = Repository(session)
    lead, reply = make_ack(repo)
    sender = FakeSender(engine, factory)
    assert make_service(factory, practice, sender).send(reply.id) is SendOutcome.SENT
    got = repo.get_reply(reply.id)
    assert (got.status, got.sent_at, got.claimed_at, got.send_attempts, got.error) == ("sent", NOW, NOW, 1, None)
    assert sender.calls == [{"to": EMAIL, "subject": templates.subject(None, practice), "body": "Danke, wir melden uns."}]


# Review focus 1. Both threads read the reply as "drafted" and both try to claim it. The claim
# (a conditional UPDATE) lets exactly one through; the sender's delay keeps the winner's send in
# flight while the loser tries. Without the claim both would send.
def test_two_sends_of_one_reply_at_once_send_one_email(session, factory, practice, engine):
    _, reply = make_ack(Repository(session))
    sender = FakeSender(engine, factory, delay=0.3)
    service = make_service(factory, practice, sender)
    barrier = threading.Barrier(2)
    outcomes = []

    def worker():
        barrier.wait()
        outcomes.append(service.send(reply.id))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(sender.calls) == 1
    assert sorted(outcomes) == sorted([SendOutcome.SENT, SendOutcome.ALREADY_HANDLED])
    got = Repository(session).get_reply(reply.id)
    assert (got.status, got.send_attempts) == ("sent", 1)


def test_sender_runs_with_no_open_transaction(session, factory, practice, engine):
    _, reply = make_ack(Repository(session))
    sender = FakeSender(engine, factory)
    make_service(factory, practice, sender).send(reply.id)
    assert len(sender.calls) == 1
    assert factory.sessions, "the service must use the session factory"
    assert sender.open_transactions == [False]
    assert sender.idle_in_transaction == [0]


def test_provider_failure_ends_failed_with_class_name_only(session, factory, practice, engine):
    repo = Repository(session)
    _, reply = make_ack(repo)
    sender = FakeSender(engine, factory, outcome=Outcome.FAILED, detail="SMTPRecipientsRefused")
    assert make_service(factory, practice, sender).send(reply.id) is SendOutcome.FAILED
    got = repo.get_reply(reply.id)
    assert (got.status, got.error, got.sent_at) == ("failed", "SMTPRecipientsRefused", None)
    assert EMAIL not in got.error


def test_timeout_leaves_reply_in_sending_and_returns_in_doubt(session, factory, practice, engine):
    repo = Repository(session)
    _, reply = make_ack(repo)
    sender = FakeSender(engine, factory, outcome=Outcome.TIMEOUT, detail="TimeoutError")
    assert make_service(factory, practice, sender).send(reply.id) is SendOutcome.IN_DOUBT
    got = repo.get_reply(reply.id)
    assert (got.status, got.claimed_at, got.send_attempts, got.error) == ("sending", NOW, 1, None)


# Probe P8: the email was accepted but the database fails when "sent" is recorded. The reply must
# stay "sending" (outcome unknown), never "failed" (that would invite a resend of a sent email).
def test_recording_failure_after_accepted_leaves_sending(session, factory, practice, engine, monkeypatch):
    repo = Repository(session)
    _, reply = make_ack(repo)
    original = Repository.set_reply_status

    def failing(self, reply_id, status, **kwargs):
        if status is ReplyStatus.SENT:
            raise OperationalError("UPDATE replies", {}, Exception("database down"))
        return original(self, reply_id, status, **kwargs)

    monkeypatch.setattr(Repository, "set_reply_status", failing)
    sender = FakeSender(engine, factory)
    assert make_service(factory, practice, sender).send(reply.id) is SendOutcome.IN_DOUBT
    monkeypatch.undo()
    assert len(sender.calls) == 1
    assert repo.get_reply(reply.id).status == "sending"


# Spec section 7: no address any more means no send and no claim; the reply becomes "failed" with
# no error text. A resend of that failed reply is refused the same way and left as it is.
def test_anonymized_lead_is_refused_and_reply_failed_without_error_text(session, factory, practice, engine):
    repo = Repository(session)
    _, reply = make_ack(repo)
    repo.anonymize_older_than(cutoff=NOW + timedelta(days=1), now=NOW)
    sender = FakeSender(engine, factory)
    service = make_service(factory, practice, sender)
    assert service.send(reply.id) is SendOutcome.REFUSED
    got = repo.get_reply(reply.id)
    assert (got.status, got.error, got.send_attempts) == ("failed", None, 0)
    assert service.send(reply.id) is SendOutcome.REFUSED
    assert repo.get_reply(reply.id).status == "failed"
    assert sender.calls == []


# Spec 11f: retention blanks the lead right after the claim committed. send reads the address
# again after the claim, finds none, sends nothing and moves the reply to "failed".
def test_lead_anonymized_right_after_the_claim_is_not_sent(session, factory, practice, engine, monkeypatch):
    repo = Repository(session)
    _, reply = make_ack(repo)
    original = Repository.set_reply_status

    def claim_then_anonymize(self, reply_id, status, **kwargs):
        result = original(self, reply_id, status, **kwargs)
        if status is ReplyStatus.SENDING:
            with Session(engine) as other:
                Repository(other).anonymize_older_than(cutoff=NOW + timedelta(days=1), now=NOW)
        return result

    monkeypatch.setattr(Repository, "set_reply_status", claim_then_anonymize)
    sender = FakeSender(engine, factory)
    assert make_service(factory, practice, sender).send(reply.id) is SendOutcome.REFUSED
    monkeypatch.undo()
    assert sender.calls == []
    got = repo.get_reply(reply.id)
    assert (got.status, got.error) == ("failed", None)


def test_draft_must_be_approved_before_send(session, factory, practice, engine):
    repo = Repository(session)
    lead = make_lead(repo)
    draft = repo.add_reply(lead.id, kind=Action.DRAFT, ai_body="KI-Entwurf", body="Entwurf",
                           status=ReplyStatus.DRAFTED, now=NOW)
    sender = FakeSender(engine, factory)
    service = make_service(factory, practice, sender)
    assert service.send(draft.id) is SendOutcome.REFUSED
    assert sender.calls == []
    assert repo.get_reply(draft.id).status == "drafted"
    repo.set_reply_status(draft.id, ReplyStatus.APPROVED, now=NOW)
    assert service.send(draft.id) is SendOutcome.SENT
    assert [c["body"] for c in sender.calls] == ["Entwurf"]


def test_auto_reply_sent_moves_lead_to_auto_replied(session, factory, practice, engine):
    repo = Repository(session)
    lead, reply = make_ack(repo, kind=Action.AUTO_REPLY)
    assert make_service(factory, practice, FakeSender(engine, factory)).send(reply.id) is SendOutcome.SENT
    got = repo.get_lead(lead.id)
    assert (got.status, got.needs_manual_review) == ("auto_replied", False)


# Spec 11k: a lead must never look handled when the patient got nothing.
@pytest.mark.parametrize("outcome, expected", [
    (Outcome.FAILED, SendOutcome.FAILED), (Outcome.TIMEOUT, SendOutcome.IN_DOUBT),
])
def test_auto_reply_failed_or_in_doubt_flags_lead_and_moves_it_to_acknowledged(
    session, factory, practice, engine, outcome, expected,
):
    repo = Repository(session)
    lead, reply = make_ack(repo, kind=Action.AUTO_REPLY)
    sender = FakeSender(engine, factory, outcome=outcome, detail="SomeError")
    assert make_service(factory, practice, sender).send(reply.id) is expected
    got = repo.get_lead(lead.id)
    assert (got.status, got.needs_manual_review) == ("acknowledged", True)


def test_failed_acknowledgment_flags_lead(session, factory, practice, engine):
    repo = Repository(session)
    lead, reply = make_ack(repo)
    assert repo.get_lead(lead.id).needs_manual_review is False
    sender = FakeSender(engine, factory, outcome=Outcome.FAILED, detail="ConnectionRefusedError")
    assert make_service(factory, practice, sender).send(reply.id) is SendOutcome.FAILED
    got = repo.get_lead(lead.id)
    assert (got.status, got.needs_manual_review) == ("acknowledged", True)


def test_resend_of_failed_reply_claims_again(session, factory, practice, engine):
    repo = Repository(session)
    _, reply = make_ack(repo)
    failing = FakeSender(engine, factory, outcome=Outcome.FAILED, detail="ConnectionRefusedError")
    assert make_service(factory, practice, failing).send(reply.id) is SendOutcome.FAILED
    working = FakeSender(engine, factory)
    assert make_service(factory, practice, working).send(reply.id) is SendOutcome.SENT
    got = repo.get_reply(reply.id)
    assert (got.status, got.send_attempts, got.error, got.sent_at) == ("sent", 2, None, NOW)
    assert len(working.calls) == 1


def test_sent_reply_is_already_handled(session, factory, practice, engine):
    _, reply = make_ack(Repository(session))
    sender = FakeSender(engine, factory)
    service = make_service(factory, practice, sender)
    assert service.send(reply.id) is SendOutcome.SENT
    assert service.send(reply.id) is SendOutcome.ALREADY_HANDLED
    assert len(sender.calls) == 1


# Route 1 fallback: the combined DE+EN template with the emergency paragraphs, the bilingual
# subject, the name cleaned by sanitize_name, and the lead flagged for staff.
def test_acknowledge_with_fallback_builds_bilingual_emergency_acknowledgment(session, factory, practice, engine):
    repo = Repository(session)
    lead, _ = repo.create_lead_if_new(
        name="Anna <b>Test</b>", email=EMAIL, phone=None, message="Hilfe", consent_at=NOW, now=NOW,
        mailbox_key=mailbox_key(EMAIL),
    )
    sender = FakeSender(engine, factory)
    assert make_service(factory, practice, sender).acknowledge_with_fallback(lead.id) is True
    [call] = sender.calls
    assert call["subject"] == templates.subject("de+en", practice)
    assert call["body"].startswith("Hallo,") and "Hello," in call["body"]
    assert "112" in call["body"] and "<b>" not in call["body"]
    got = repo.get_lead(lead.id)
    assert (got.status, got.needs_manual_review) == ("acknowledged", True)
    [reply] = repo.replies_for(lead.id)
    assert (reply.kind, reply.status) == ("acknowledgment", "sent")


# Review focus 2 seen from the service: two fallbacks for one lead (deadline vs sweep) at once.
# acknowledge_lead's lock lets one write the acknowledgment; the other gets False and sends nothing.
def test_acknowledge_with_fallback_sends_once_when_called_twice(session, factory, practice, engine):
    lead = make_lead(Repository(session))
    sender = FakeSender(engine, factory, delay=0.2)
    service = make_service(factory, practice, sender)
    barrier = threading.Barrier(2)
    results = []

    def worker():
        barrier.wait()
        results.append(service.acknowledge_with_fallback(lead.id))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results) == [False, True]
    assert len(sender.calls) == 1
    assert len(Repository(session).replies_for(lead.id)) == 1
