"""Replies repository tests: storing, ordering, status transitions, validation, FK failure."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from lead_responder.db.models import ReplyStatus
from lead_responder.repository import Repository
from lead_responder.router import Action

NOW = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)


# Same style as the leads tests: a real lead row, because replies have a foreign key to it.
def make_lead(repo: Repository, **overrides):
    fields = dict(
        name="Anna Test", email="anna@example.com", phone="0511 123456",
        message="Ich moechte einen Termin.", consent_at=NOW, now=NOW,
    )
    fields.update(overrides)
    lead, _ = repo.create_lead_if_new(**fields)
    return lead


def add(repo: Repository, lead_id: int, **overrides):
    fields = dict(
        kind=Action.DRAFT, ai_body="AI text", body="AI text",
        status=ReplyStatus.DRAFTED, now=NOW,
    )
    fields.update(overrides)
    return repo.add_reply(lead_id, **fields)


def test_add_reply_stores_ai_body_and_body_separately(session):
    repo = Repository(session)
    lead = make_lead(repo)
    reply = add(repo, lead.id, ai_body="Original", body="Edited by staff", edited=True)
    session.expire_all()
    stored = repo.replies_for(lead.id)[0]
    assert stored.id == reply.id
    assert stored.ai_body == "Original"
    assert stored.body == "Edited by staff"
    assert stored.kind == "draft"
    assert stored.status == "drafted"
    assert stored.created_at == NOW
    assert stored.edited is True
    assert stored.sent_at is None


def test_add_reply_ai_body_may_be_none(session):
    repo = Repository(session)
    lead = make_lead(repo)
    reply = add(repo, lead.id, kind=Action.ACKNOWLEDGMENT, ai_body=None, body="Danke, wir melden uns.")
    assert reply.ai_body is None
    assert reply.edited is None


def test_replies_for_returns_in_creation_order(session):
    repo = Repository(session)
    lead = make_lead(repo)
    other = make_lead(repo, email="bob@example.com")
    # Inserted out of time order on purpose; two share a timestamp so the id tie-break counts.
    late = add(repo, lead.id, body="late", now=NOW + timedelta(minutes=5))
    early = add(repo, lead.id, body="early", now=NOW)
    tie = add(repo, lead.id, body="tie", now=NOW)
    add(repo, other.id, body="other lead")
    assert [r.id for r in repo.replies_for(lead.id)] == [early.id, tie.id, late.id]


def test_replies_for_unknown_lead_is_empty(session):
    assert Repository(session).replies_for(999_999) == []


def test_set_reply_status_sent_sets_sent_at(session):
    repo = Repository(session)
    reply = add(repo, make_lead(repo).id)
    later = NOW + timedelta(minutes=1)
    updated = repo.set_reply_status(reply.id, ReplyStatus.SENT, now=later)
    session.expire_all()
    assert updated.status == "sent"
    assert updated.sent_at == later


def test_set_reply_status_other_statuses_leave_sent_at_empty(session):
    repo = Repository(session)
    reply = add(repo, make_lead(repo).id)
    for status in (ReplyStatus.APPROVED, ReplyStatus.REJECTED, ReplyStatus.FAILED):
        updated = repo.set_reply_status(reply.id, status, now=NOW + timedelta(minutes=1))
        assert updated.status == status.value
        assert updated.sent_at is None


def test_failed_reply_keeps_error_text(session):
    repo = Repository(session)
    reply = add(repo, make_lead(repo).id)
    repo.set_reply_status(reply.id, ReplyStatus.FAILED, now=NOW, error="timeout: may have been sent")
    session.expire_all()
    stored = repo.replies_for(reply.lead_id)[0]
    assert stored.status == "failed"
    assert stored.error == "timeout: may have been sent"


def test_successful_resend_clears_old_error(session):
    repo = Repository(session)
    reply = add(repo, make_lead(repo).id)
    repo.set_reply_status(reply.id, ReplyStatus.FAILED, now=NOW, error="smtp down")
    repo.set_reply_status(reply.id, ReplyStatus.SENT, now=NOW)
    assert reply.error is None


def test_set_reply_status_records_approver_and_edited(session):
    from lead_responder.db.models import StaffUser

    repo = Repository(session)
    reply = add(repo, make_lead(repo).id)
    user = StaffUser(name="Dr. Lang", email="lang@example.com", password_hash="x")
    session.add(user)
    session.commit()
    repo.set_reply_status(reply.id, ReplyStatus.APPROVED, now=NOW, approved_by=user.id, edited=False)
    session.expire_all()
    stored = repo.replies_for(reply.lead_id)[0]
    assert stored.approved_by == user.id
    assert stored.edited is False
    # A later transition that passes neither must not wipe them.
    repo.set_reply_status(reply.id, ReplyStatus.SENT, now=NOW)
    session.expire_all()
    stored = repo.replies_for(reply.lead_id)[0]
    assert stored.approved_by == user.id
    assert stored.edited is False


def test_set_reply_status_unknown_id_raises_lookup_error(session):
    repo = Repository(session)
    with pytest.raises(LookupError):
        repo.set_reply_status(999_999, ReplyStatus.SENT, now=NOW)
    # Session still usable afterwards.
    assert repo.replies_for(1) == []


def test_reply_for_missing_lead_raises(session):
    repo = Repository(session)
    with pytest.raises(IntegrityError):
        add(repo, 999_999)
    # The failed commit was rolled back, so the same session keeps working.
    lead = make_lead(repo)
    reply = add(repo, lead.id)
    assert reply.id is not None
    assert len(repo.replies_for(lead.id)) == 1


def test_kind_and_status_must_be_enum_members(session):
    repo = Repository(session)
    lead = make_lead(repo)
    with pytest.raises(ValueError):
        add(repo, lead.id, kind="banana")
    with pytest.raises(ValueError):
        add(repo, lead.id, status="banana")
    reply = add(repo, lead.id)
    with pytest.raises(ValueError):
        repo.set_reply_status(reply.id, "banana", now=NOW)
    assert len(repo.replies_for(lead.id)) == 1
    assert reply.status == "drafted"


def test_naive_datetimes_are_rejected(session):
    repo = Repository(session)
    lead = make_lead(repo)
    naive = datetime(2026, 10, 23, 10, 0)
    with pytest.raises(ValueError):
        add(repo, lead.id, now=naive)
    reply = add(repo, lead.id)
    with pytest.raises(ValueError):
        repo.set_reply_status(reply.id, ReplyStatus.SENT, now=naive)
    assert reply.sent_at is None


def test_hostile_text_in_bodies(session):
    repo = Repository(session)
    lead = make_lead(repo)
    reply = add(repo, lead.id, ai_body="a\x00b", body="Robert'); DROP TABLE leads;-- \U0001F600")
    assert reply.ai_body == "ab"
    assert reply.body == "Robert'); DROP TABLE leads;-- \U0001F600"
