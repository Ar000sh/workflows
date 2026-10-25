"""First acknowledgment, conditional classification, suppress and flag (A1 layer 4b, spec section 4).

The rule under test: a lead gets at most ONE first reply (acknowledgment or auto-reply), and the
lead-status move and the reply insert happen together or not at all. Several writers can try at
once (the deadline fallback, a late pipeline, two overlapping sweeps), so the races run for real
with two sessions.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from lead_responder.db.models import Lead, LeadStatus, Reply
from lead_responder.limits import mailbox_key
from lead_responder.models import Category, Classification, Risk, Urgency
from lead_responder.repository import InvalidTransition, Repository
from lead_responder.router import Action, Decision

NOW = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)


def make_lead(repo: Repository, email="anna@example.com", **overrides) -> Lead:
    fields = dict(
        name="Anna Test", email=email, phone="0511 123456", message="Ich habe Zahnschmerzen.",
        consent_at=NOW, now=NOW, mailbox_key=mailbox_key(email),
    )
    fields.update(overrides)
    lead, _ = repo.create_lead_if_new(**fields)
    return lead


def ack(repo: Repository, lead_id: int, kind=Action.ACKNOWLEDGMENT, **overrides) -> Reply:
    fields = dict(kind=kind, ai_body=None, body="Danke, wir melden uns.", now=NOW)
    fields.update(overrides)
    return repo.acknowledge_lead(lead_id, **fields)


def first_replies(session, lead_id: int) -> list[Reply]:
    session.expire_all()
    return list(session.scalars(select(Reply).where(Reply.lead_id == lead_id)))


def _cls() -> Classification:
    return Classification(
        category=Category.APPOINTMENT, risk=Risk.ROUTINE, urgency=Urgency.NORMAL,
        language="de", reason="Terminwunsch",
    )


def _decision() -> Decision:
    return Decision(
        risk=Risk.ROUTINE, actions=(Action.AUTO_REPLY,), language="de", emergency=False,
        language_unsupported=False, needs_manual_review=False,
    )


# --- acknowledge_lead ---

def test_acknowledge_lead_moves_status_and_inserts_reply_in_one_transaction(session):
    repo = Repository(session)
    lead = make_lead(repo)
    repo.set_lead_status(lead.id, LeadStatus.CLASSIFIED)  # a sensitive lead after classification
    reply = ack(repo, lead.id, needs_manual_review=True)
    assert (reply.kind, reply.status, reply.body, reply.created_at) == (
        "acknowledgment", "drafted", "Danke, wir melden uns.", NOW,
    )
    got = repo.get_lead(lead.id)
    assert got.status == "acknowledged"
    assert got.needs_manual_review is True
    assert [r.id for r in first_replies(session, lead.id)] == [reply.id]


# Atomicity: the status UPDATE has already run when the reply INSERT fails (lead_id set to NULL
# breaks NOT NULL at flush). The rollback must take the status move with it.
def test_acknowledge_lead_failure_leaves_status_unchanged(session, monkeypatch):
    repo = Repository(session)
    lead = make_lead(repo)
    real_add = session.add

    def broken_add(obj, *args, **kwargs):
        if isinstance(obj, Reply):
            obj.lead_id = None
        return real_add(obj, *args, **kwargs)

    monkeypatch.setattr(session, "add", broken_add)
    with pytest.raises(Exception):  # noqa: B017  (IntegrityError from the database)
        ack(repo, lead.id, needs_manual_review=True)
    monkeypatch.undo()
    got = repo.get_lead(lead.id)
    assert got.status == "new"
    assert got.needs_manual_review is False
    assert first_replies(session, lead.id) == []


# The core race (probe P1): two writers of the first acknowledgment at the same moment. Both lock
# the lead row FOR NO KEY UPDATE, so they take turns; the second sees the first one's reply and
# gets InvalidTransition. Exactly one reply row, so the patient gets exactly one email.
def test_two_sessions_acknowledging_one_lead_exactly_one_wins(engine, session):
    repo = Repository(session)
    for i in range(10):
        lead = make_lead(repo, email=f"p{i}@example.com")
        barrier = threading.Barrier(2)

        def attempt(_, lead_id=lead.id, barrier=barrier):
            with Session(engine, expire_on_commit=False) as s:
                barrier.wait(timeout=5)
                try:
                    ack(Repository(s), lead_id)
                    return "won"
                except InvalidTransition:
                    return "lost"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(pool.map(attempt, range(2)))
        assert results == ["lost", "won"]
        assert len(first_replies(session, lead.id)) == 1
        assert repo.get_lead(lead.id).status == "acknowledged"


# The sweep case: a lead already "acknowledged" with no first reply (its status was moved by
# set_lead_status and the writer died before the reply). It gets one acknowledgment, the status
# stays, and a second call is refused.
def test_acknowledge_lead_on_acknowledged_lead_without_reply_works_once(session):
    repo = Repository(session)
    lead = make_lead(repo)
    repo.set_lead_status(lead.id, LeadStatus.ACKNOWLEDGED)
    ack(repo, lead.id)
    assert repo.get_lead(lead.id).status == "acknowledged"
    with pytest.raises(InvalidTransition):
        ack(repo, lead.id)
    assert len(first_replies(session, lead.id)) == 1


def test_auto_reply_kind_does_not_move_status(session):
    repo = Repository(session)
    lead = make_lead(repo)
    repo.set_lead_status(lead.id, LeadStatus.CLASSIFIED)
    reply = ack(repo, lead.id, kind=Action.AUTO_REPLY, ai_body="AI text", body="AI text")
    assert (reply.kind, reply.ai_body) == ("auto_reply", "AI text")
    assert repo.get_lead(lead.id).status == "classified"
    # An auto-reply counts as the first reply: no acknowledgment after it.
    with pytest.raises(InvalidTransition):
        ack(repo, lead.id)


@pytest.mark.parametrize("path", [
    (LeadStatus.CLASSIFIED, LeadStatus.AUTO_REPLIED),
    (LeadStatus.ACKNOWLEDGED, LeadStatus.AWAITING_APPROVAL),
    (LeadStatus.ACKNOWLEDGED, LeadStatus.DONE),
])
def test_acknowledge_lead_refuses_leads_past_acknowledged(session, path):
    repo = Repository(session)
    lead = make_lead(repo)
    for status in path:
        repo.set_lead_status(lead.id, status)
    for kind in (Action.ACKNOWLEDGMENT, Action.AUTO_REPLY):
        with pytest.raises(InvalidTransition):
            ack(repo, lead.id, kind=kind)
    assert first_replies(session, lead.id) == []
    assert repo.get_lead(lead.id).status == path[-1].value


def test_acknowledge_lead_rejects_draft_kind_and_naive_now(session):
    repo = Repository(session)
    lead = make_lead(repo)
    with pytest.raises(ValueError):
        ack(repo, lead.id, kind=Action.DRAFT)
    with pytest.raises(ValueError):
        ack(repo, lead.id, kind="acknowledgment")
    with pytest.raises(ValueError):
        ack(repo, lead.id, now=datetime(2026, 10, 24, 10, 0))
    assert first_replies(session, lead.id) == []
    assert repo.get_lead(lead.id).status == "new"


def test_acknowledge_lead_refuses_anonymized_and_unknown_leads(session):
    repo = Repository(session)
    lead = make_lead(repo)
    repo.anonymize_older_than(cutoff=NOW + timedelta(days=1), now=NOW + timedelta(days=1))
    with pytest.raises(ValueError, match="anonymized"):
        ack(repo, lead.id)
    with pytest.raises(LookupError):
        ack(repo, 999_999)
    assert first_replies(session, lead.id) == []


# A lead the per-address limit left without email on purpose gets no acknowledgment from any
# writer (a sweep that read the lead just before suppress_lead ran, or the deadline fallback).
def test_acknowledge_lead_refuses_a_suppressed_lead(session):
    repo = Repository(session)
    lead = make_lead(repo)
    repo.suppress_lead(lead.id)
    with pytest.raises(InvalidTransition, match="suppressed"):
        ack(repo, lead.id)
    assert first_replies(session, lead.id) == []
    assert repo.get_lead(lead.id).status == "acknowledged"


# A draft (staff reply) is not a first reply: it does not block the acknowledgment.
def test_a_draft_reply_does_not_count_as_first_reply(session):
    from lead_responder.db.models import ReplyStatus

    repo = Repository(session)
    lead = make_lead(repo)
    repo.add_reply(lead.id, kind=Action.DRAFT, ai_body="x", body="x", status=ReplyStatus.DRAFTED, now=NOW)
    ack(repo, lead.id)
    assert repo.get_lead(lead.id).status == "acknowledged"


# --- set_classification is conditional on status "new" (discarding a late pipeline result) ---

def test_set_classification_refused_after_status_moved(session):
    repo = Repository(session)
    lead = make_lead(repo)
    repo.set_classification(lead.id, classification=_cls(), decision=_decision())  # still works on new
    assert repo.get_lead(lead.id).ai_reason == "Terminwunsch"
    ack(repo, lead.id)  # the deadline fallback acknowledged it
    late = Classification(
        category=Category.PAIN_EMERGENCY, risk=Risk.SENSITIVE, urgency=Urgency.HIGH,
        language="de", reason="late",
    )
    with pytest.raises(InvalidTransition):
        repo.set_classification(lead.id, classification=late, decision=_decision())
    got = repo.get_lead(lead.id)
    assert (got.ai_reason, got.category) == ("Terminwunsch", "appointment")


# Deterministic race: another session is in the middle of acknowledging the lead (row locked,
# status moved, not committed yet). set_classification must WAIT for that lock, then see the new
# status and write nothing. A check before the lock would see "new" and store the late result.
def test_set_classification_waits_for_an_acknowledgment_then_refuses(engine, session):
    repo = Repository(session)
    lead = make_lead(repo)
    outcome = {}

    def worker():
        with Session(engine, expire_on_commit=False) as s:
            try:
                Repository(s).set_classification(lead.id, classification=_cls(), decision=_decision())
                outcome["result"] = "stored"
            except InvalidTransition:
                outcome["result"] = "refused"

    with Session(engine) as holder:
        holder.execute(select(Lead.id).where(Lead.id == lead.id).with_for_update(key_share=True))
        holder.execute(update(Lead).where(Lead.id == lead.id).values(status="acknowledged"))
        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=0.5)
        assert t.is_alive(), "set_classification did not wait for the lead's row lock"
        holder.commit()
    t.join(timeout=10)
    assert outcome["result"] == "refused"
    assert repo.get_lead(lead.id).ai_reason is None


# --- suppress_lead and flag_for_review ---

def test_suppress_lead_flags_and_acknowledges(session):
    repo = Repository(session)
    lead = make_lead(repo)
    got = repo.suppress_lead(lead.id)
    assert (got.status, got.send_suppressed, got.needs_manual_review) == ("acknowledged", True, True)
    assert first_replies(session, lead.id) == []
    with pytest.raises(InvalidTransition):
        repo.suppress_lead(lead.id)  # only from "new"


def test_suppress_lead_refuses_a_classified_lead(session):
    repo = Repository(session)
    lead = make_lead(repo)
    repo.set_lead_status(lead.id, LeadStatus.CLASSIFIED)
    with pytest.raises(InvalidTransition):
        repo.suppress_lead(lead.id)
    got = repo.get_lead(lead.id)
    assert (got.status, got.send_suppressed, got.needs_manual_review) == ("classified", False, False)


def test_suppress_lead_refuses_anonymized_and_unknown_leads(session):
    repo = Repository(session)
    lead = make_lead(repo)
    repo.anonymize_older_than(cutoff=NOW + timedelta(days=1), now=NOW + timedelta(days=1))
    with pytest.raises(ValueError, match="anonymized"):
        repo.suppress_lead(lead.id)
    with pytest.raises(LookupError):
        repo.suppress_lead(999_999)


def test_flag_for_review_keeps_status(session):
    repo = Repository(session)
    lead = make_lead(repo)
    repo.set_lead_status(lead.id, LeadStatus.CLASSIFIED)
    got = repo.flag_for_review(lead.id)
    assert (got.status, got.needs_manual_review) == ("classified", True)
    # Allowed on an anonymized lead: a flag is not personal data.
    repo.anonymize_older_than(cutoff=NOW + timedelta(days=1), now=NOW + timedelta(days=1))
    other = make_lead(repo, email="b@example.com", now=NOW + timedelta(days=2))
    assert repo.flag_for_review(lead.id).needs_manual_review is True
    assert repo.get_lead(other.id).needs_manual_review is False
    with pytest.raises(LookupError):
        repo.flag_for_review(999_999)


# --- get_reply ---

def test_get_reply_reads_fresh_and_returns_none_for_unknown_id(engine, session):
    from lead_responder.db.models import ReplyStatus

    repo = Repository(session)
    reply = ack(repo, make_lead(repo).id)
    assert repo.get_reply(reply.id).status == "drafted"
    with Session(engine) as other:
        Repository(other).set_reply_status(reply.id, ReplyStatus.SENDING, now=NOW)
    assert repo.get_reply(reply.id).status == "sending"
    assert not session.in_transaction()
    assert repo.get_reply(999_999) is None
