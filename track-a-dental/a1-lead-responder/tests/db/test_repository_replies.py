"""Replies repository tests: storing, ordering, status transitions, validation, FK failure."""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_responder.db.models import Reply, ReplyStatus
from lead_responder.repository import InvalidTransition, Repository
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
    # One reply per target status: moves out of "rejected" are no longer allowed.
    for status in (ReplyStatus.APPROVED, ReplyStatus.REJECTED, ReplyStatus.FAILED):
        updated = repo.set_reply_status(
            add(repo, reply.lead_id).id, status, now=NOW + timedelta(minutes=1),
        )
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


# --- Status transitions: only the moves in REPLY_TRANSITIONS are allowed, enforced by ONE
# conditional UPDATE (WHERE status IN allowed-sources), so two sessions cannot both win. ---

def test_sent_is_final(session):
    repo = Repository(session)
    reply = add(repo, make_lead(repo).id)
    repo.set_reply_status(reply.id, ReplyStatus.SENT, now=NOW)
    for status in ReplyStatus:
        with pytest.raises(InvalidTransition):
            repo.set_reply_status(reply.id, status, now=NOW + timedelta(minutes=5), error="late")
    session.expire_all()
    stored = repo.replies_for(reply.lead_id)[0]
    assert stored.status == "sent"
    assert stored.sent_at == NOW  # a rejected move must not touch sent_at
    assert stored.error is None  # nor the other fields


def test_rejected_is_final(session):
    repo = Repository(session)
    reply = add(repo, make_lead(repo).id)
    repo.set_reply_status(reply.id, ReplyStatus.REJECTED, now=NOW)
    with pytest.raises(InvalidTransition):
        repo.set_reply_status(reply.id, ReplyStatus.APPROVED, now=NOW)


def test_the_normal_approval_path_is_allowed(session):
    repo = Repository(session)
    reply = add(repo, make_lead(repo).id)
    for status in (ReplyStatus.APPROVED, ReplyStatus.SENT):
        assert repo.set_reply_status(reply.id, status, now=NOW).status == status.value


def test_failed_can_be_resent_or_fail_again(session):
    repo = Repository(session)
    reply = add(repo, make_lead(repo).id)
    repo.set_reply_status(reply.id, ReplyStatus.FAILED, now=NOW, error="smtp down")
    repo.set_reply_status(reply.id, ReplyStatus.FAILED, now=NOW, error="still down")
    assert repo.set_reply_status(reply.id, ReplyStatus.SENT, now=NOW).status == "sent"


def test_same_status_is_refused(session):
    repo = Repository(session)
    reply = add(repo, make_lead(repo).id)
    with pytest.raises(InvalidTransition):
        repo.set_reply_status(reply.id, ReplyStatus.DRAFTED, now=NOW)  # drafted -> drafted
    repo.set_reply_status(reply.id, ReplyStatus.APPROVED, now=NOW)
    with pytest.raises(InvalidTransition):
        repo.set_reply_status(reply.id, ReplyStatus.APPROVED, now=NOW)  # approved -> approved


def test_refused_move_does_not_write_approver_or_edited(session):
    repo = Repository(session)
    reply = add(repo, make_lead(repo).id)
    repo.set_reply_status(reply.id, ReplyStatus.REJECTED, now=NOW)
    with pytest.raises(InvalidTransition):
        repo.set_reply_status(reply.id, ReplyStatus.APPROVED, now=NOW, approved_by=None, edited=True)
    session.expire_all()
    assert repo.replies_for(reply.lead_id)[0].edited is None


def test_unknown_reply_is_still_a_lookup_error_not_a_transition_error(session):
    with pytest.raises(LookupError):
        Repository(session).set_reply_status(999_999, ReplyStatus.SENT, now=NOW)


# The stale-view case that the old read-then-write code lost: session B still believes the reply
# is "approved" when session A has already sent it. B's own session may not send it a second time.
def test_stale_session_cannot_send_twice(engine, session):
    repo_a = Repository(session)
    reply = add(repo_a, make_lead(repo_a).id)
    repo_a.set_reply_status(reply.id, ReplyStatus.APPROVED, now=NOW)
    with Session(engine, expire_on_commit=False) as other:
        repo_b = Repository(other)
        seen = other.get(Reply, reply.id)
        assert seen.status == "approved"  # B's cached view
        repo_a.set_reply_status(reply.id, ReplyStatus.SENT, now=NOW)
        with pytest.raises(InvalidTransition):
            repo_b.set_reply_status(reply.id, ReplyStatus.SENT, now=NOW + timedelta(minutes=1))
    session.expire_all()
    assert session.get(Reply, reply.id).sent_at == NOW  # the first send's timestamp survives


# Real race: both threads try approved -> sent at the same moment. The UPDATE's row lock makes
# the second one wait, re-check the WHERE and find 0 rows, so exactly one wins.
def test_two_threads_racing_to_send_exactly_one_wins(engine, session):
    repo = Repository(session)
    reply = add(repo, make_lead(repo).id)
    repo.set_reply_status(reply.id, ReplyStatus.APPROVED, now=NOW)
    barrier = threading.Barrier(2)

    def attempt(_):
        with Session(engine, expire_on_commit=False) as s:
            barrier.wait()
            try:
                Repository(s).set_reply_status(reply.id, ReplyStatus.SENT, now=NOW)
                return "won"
            except InvalidTransition:
                return "lost"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(attempt, range(2)))
    assert results == ["lost", "won"]
