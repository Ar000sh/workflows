"""Retention (DSGVO) tests: personal data blanked, statistics kept, exact boundary, idempotent."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from lead_responder import retention
from lead_responder.db.models import AuditLog, Lead, LeadStatus, Reply, ReplyStatus
from lead_responder.repository import Repository
from lead_responder.router import Action

NOW = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)
CUTOFF = NOW - timedelta(days=90)


# A lead created at `created` (so we control its age) with an optional reply.
def make_lead(repo: Repository, created: datetime, email="anna@example.com", **kw):
    lead, _ = repo.create_lead_if_new(
        name="Anna Test", email=email, phone="0511 123456",
        message="Ich habe Zahnschmerzen.", consent_at=created, now=created, **kw,
    )
    return lead


def add_reply(repo: Repository, lead_id: int, n: int = 1, status: ReplyStatus = ReplyStatus.SENT):
    return repo.add_reply(
        lead_id, kind=Action.DRAFT, ai_body=f"AI text {n}", body=f"Edited text {n}",
        status=status, now=NOW, edited=True,
    )


def test_personal_fields_are_blanked_and_statistics_kept(session):
    repo = Repository(session)
    lead = make_lead(repo, CUTOFF - timedelta(days=1))
    lead.category, lead.risk, lead.urgency, lead.language = "appointment", "low", "normal", "de"
    lead.ai_reason = "Patient nennt Zahnschmerzen"  # free text about the patient: must go
    lead.status = LeadStatus.REPLIED.value  # set directly: this test is about retention, not the flow
    session.commit()

    assert repo.anonymize_older_than(cutoff=CUTOFF, now=NOW) == 1

    session.expire_all()
    got = repo.get_lead(lead.id)
    assert (got.name, got.email, got.phone, got.message) == ("[anonymized]", "", None, "")
    assert got.anonymized_at == NOW
    assert got.ai_reason is None
    assert (got.category, got.risk, got.urgency, got.language) == ("appointment", "low", "normal", "de")
    assert got.status == "replied"
    assert got.created_at == CUTOFF - timedelta(days=1)


def test_reply_texts_and_dedup_key_are_blanked_too(session):
    repo = Repository(session)
    lead = make_lead(repo, CUTOFF - timedelta(days=1))
    assert lead.dedup_key is not None
    # Created as drafts: "sent" is final, so the failed-then-sent resends below start from "drafted".
    r1 = add_reply(repo, lead.id, 1, ReplyStatus.DRAFTED)
    r2 = add_reply(repo, lead.id, 2, ReplyStatus.DRAFTED)
    # a failed send may leave the recipient address in `error`
    repo.set_reply_status(r1.id, ReplyStatus.FAILED, now=NOW, error="550 rejected: anna@example.com")
    repo.set_reply_status(r1.id, ReplyStatus.SENT, now=NOW)
    repo.set_reply_status(r2.id, ReplyStatus.FAILED, now=NOW, error="550 rejected: anna@example.com")
    repo.set_reply_status(r2.id, ReplyStatus.SENT, now=NOW, error="boom anna@example.com")
    lonely = make_lead(repo, CUTOFF - timedelta(days=2), email="b@example.com")  # no replies

    assert repo.anonymize_older_than(cutoff=CUTOFF, now=NOW) == 2

    session.expire_all()
    assert repo.get_lead(lead.id).dedup_key is None
    assert repo.get_lead(lonely.id).dedup_key is None
    for rid in (r1.id, r2.id):
        rep = session.get(Reply, rid)
        assert rep.body == "[anonymized]" and rep.ai_body is None and rep.error is None
        assert rep.status == "sent" and rep.edited is True and rep.kind == "draft"
        assert rep.sent_at == NOW and rep.created_at == NOW


def test_exact_boundary(session):
    repo = Repository(session)
    on_cutoff = make_lead(repo, CUTOFF, email="a@example.com")
    older = make_lead(repo, CUTOFF - timedelta(seconds=1), email="b@example.com")
    add_reply(repo, on_cutoff.id)

    assert repo.anonymize_older_than(cutoff=CUTOFF, now=NOW) == 1

    session.expire_all()
    assert repo.get_lead(on_cutoff.id).anonymized_at is None
    assert repo.get_lead(on_cutoff.id).email == "a@example.com"
    assert session.scalars(select(Reply)).one().body == "Edited text 1"
    assert repo.get_lead(older.id).anonymized_at == NOW


def test_young_leads_are_untouched(session):
    repo = Repository(session)
    young = make_lead(repo, NOW - timedelta(days=1))
    add_reply(repo, young.id)

    assert repo.anonymize_older_than(cutoff=CUTOFF, now=NOW) == 0

    session.expire_all()
    got = repo.get_lead(young.id)
    assert got.email == "anna@example.com" and got.dedup_key is not None and got.anonymized_at is None
    assert session.scalars(select(Reply)).one().ai_body == "AI text 1"


def test_second_run_changes_nothing(session):
    repo = Repository(session)
    lead = make_lead(repo, CUTOFF - timedelta(days=1))
    add_reply(repo, lead.id)
    assert repo.anonymize_older_than(cutoff=CUTOFF, now=NOW) == 1

    assert repo.anonymize_older_than(cutoff=CUTOFF, now=NOW + timedelta(days=1)) == 0

    session.expire_all()
    assert repo.get_lead(lead.id).anonymized_at == NOW  # not moved to the second run's time


def test_audit_rows_are_kept(session):
    repo = Repository(session)
    lead = make_lead(repo, CUTOFF - timedelta(days=1))
    reply = add_reply(repo, lead.id)
    session.add(AuditLog(action="approve", lead_id=lead.id, reply_id=reply.id, details={"x": 1}, created_at=NOW))
    session.commit()

    repo.anonymize_older_than(cutoff=CUTOFF, now=NOW)

    session.expire_all()
    row = session.scalars(select(AuditLog)).one()
    assert (row.action, row.lead_id, row.reply_id, row.details, row.created_at) == (
        "approve", lead.id, reply.id, {"x": 1}, NOW,
    )


def test_naive_datetimes_are_rejected(session):
    repo = Repository(session)
    with pytest.raises(ValueError):
        repo.anonymize_older_than(cutoff=datetime(2026, 1, 1), now=NOW)
    with pytest.raises(ValueError):
        repo.anonymize_older_than(cutoff=CUTOFF, now=datetime(2026, 1, 1))


# run() must compute the cutoff as now - days: 89 days old stays, 91 days old goes.
def test_run_uses_now_minus_days_as_cutoff(session):
    repo = Repository(session)
    keep = make_lead(repo, NOW - timedelta(days=89), email="a@example.com")
    drop = make_lead(repo, NOW - timedelta(days=91), email="b@example.com")

    assert retention.run(repo, days=90, now=NOW) == 1

    session.expire_all()
    assert repo.get_lead(keep.id).anonymized_at is None
    assert repo.get_lead(drop.id).anonymized_at == NOW


def test_run_rejects_non_positive_days(session):
    with pytest.raises(ValueError):
        retention.run(Repository(session), days=0, now=NOW)


# F1: retention works in batches (one short transaction each) so a big backlog never builds one
# huge IN list (Postgres allows 65535 bind parameters). Tiny batch size proves the loop.
def test_retention_processes_all_leads_in_batches(session):
    repo = Repository(session)
    leads = [make_lead(repo, CUTOFF - timedelta(days=1), email=f"p{i}@example.com") for i in range(7)]
    for lead in leads:
        add_reply(repo, lead.id)
    young = make_lead(repo, NOW - timedelta(days=1), email="young@example.com")
    add_reply(repo, young.id)

    assert repo.anonymize_older_than(cutoff=CUTOFF, now=NOW, batch_size=3) == 7

    session.expire_all()
    assert all(repo.get_lead(lead.id).anonymized_at == NOW for lead in leads)
    bodies = {r.lead_id: r.body for r in session.scalars(select(Reply))}
    assert all(bodies[lead.id] == "[anonymized]" for lead in leads)
    assert bodies[young.id] == "Edited text 1"
    assert repo.anonymize_older_than(cutoff=CUTOFF, now=NOW, batch_size=3) == 0


# Batches commit separately: this counts the commits to show the work really is split.
def test_retention_commits_once_per_batch(session):
    from sqlalchemy import event

    repo = Repository(session)
    for i in range(7):
        make_lead(repo, CUTOFF - timedelta(days=1), email=f"p{i}@example.com")
    commits = []
    event.listen(session, "after_commit", lambda s: commits.append(1))
    repo.anonymize_older_than(cutoff=CUTOFF, now=NOW, batch_size=3)
    assert len(commits) >= 3  # batches of 3, 3, 1 (plus the closing commit)


def test_batch_size_must_be_positive(session):
    with pytest.raises(ValueError):
        Repository(session).anonymize_older_than(cutoff=CUTOFF, now=NOW, batch_size=0)


# --- Personal data must never land on an anonymized lead. There is no clean-up pass: the write
# paths check under a lock (FOR SHARE) and refuse or drop the text. ---

# What one retention batch does, left UNcommitted: lock the lead row (FOR UPDATE) and blank it.
# Returns the open Session; the test commits it when it wants the batch to "finish".
def start_retention_batch(engine, lead_id: int) -> Session:
    batch = Session(engine)
    batch.execute(select(Lead.id).where(Lead.id == lead_id).with_for_update())
    batch.execute(update(Lead).where(Lead.id == lead_id).values(
        name="[anonymized]", email="", message="", anonymized_at=NOW))
    return batch


# Runs `write(repo)` in another thread while the batch is still open. The thread must be BLOCKED
# (waiting for the lock), then the batch commits and we return what the write did.
def write_while_batch_is_open(engine, batch: Session, write) -> str:
    outcome = {}

    def worker():
        with Session(engine, expire_on_commit=False) as s:
            try:
                write(Repository(s))
                outcome["result"] = "stored"
            except ValueError as e:
                outcome["result"] = f"refused: {e}"

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(1.0)
    assert t.is_alive(), "the write did not wait for the retention batch"
    batch.commit()
    batch.close()
    t.join(10)
    assert not t.is_alive()
    return outcome["result"]


# The race that a plain "is it anonymized?" read cannot see: the check must take a lock, so it
# waits for the running batch and then sees the lead as anonymized.
def test_add_reply_during_a_retention_batch_waits_then_refuses(engine, session):
    repo = Repository(session)
    lead = make_lead(repo, CUTOFF - timedelta(days=1))
    batch = start_retention_batch(engine, lead.id)

    result = write_while_batch_is_open(engine, batch, lambda r: r.add_reply(
        lead.id, kind=Action.DRAFT, ai_body="x", body="Hallo Anna, bei Zahnschmerzen ...",
        status=ReplyStatus.DRAFTED, now=NOW,
    ))

    assert result.startswith("refused")
    session.expire_all()
    assert session.scalars(select(Reply).where(Reply.lead_id == lead.id)).all() == []


def test_set_classification_during_a_retention_batch_waits_then_refuses(engine, session):
    from lead_responder.models import Category, Classification, Risk, Urgency
    from lead_responder.router import Decision

    repo = Repository(session)
    lead = make_lead(repo, CUTOFF - timedelta(days=1))
    batch = start_retention_batch(engine, lead.id)
    decision = Decision(
        risk=Risk.SENSITIVE, actions=(Action.DRAFT,), language="de", emergency=False,
        language_unsupported=False, needs_manual_review=False,
    )
    cls = Classification(
        category=Category.PAIN_EMERGENCY, risk=Risk.SENSITIVE, urgency=Urgency.HIGH,
        language="de", reason="Patient Anna hat Angst",
    )

    result = write_while_batch_is_open(
        engine, batch, lambda r: r.set_classification(lead.id, classification=cls, decision=decision),
    )

    assert result.startswith("refused")
    session.expire_all()
    assert session.get(Lead, lead.id).ai_reason is None


# A failed send can carry the recipient address in `error`. For an anonymized lead the status still
# moves (statistics), but the error text is dropped instead of stored.
def test_error_text_is_not_stored_for_an_anonymized_lead(session):
    repo = Repository(session)
    lead = make_lead(repo, CUTOFF - timedelta(days=1))
    reply = add_reply(repo, lead.id, status=ReplyStatus.DRAFTED)
    repo.anonymize_older_than(cutoff=CUTOFF, now=NOW)

    updated = repo.set_reply_status(reply.id, ReplyStatus.FAILED, now=NOW, error="smtp rejected x@y.de")

    assert updated.status == "failed"
    session.expire_all()
    assert session.get(Reply, reply.id).error is None


def test_error_text_during_a_retention_batch_waits_then_is_dropped(engine, session):
    repo = Repository(session)
    lead = make_lead(repo, CUTOFF - timedelta(days=1))
    reply = add_reply(repo, lead.id, status=ReplyStatus.DRAFTED)
    batch = start_retention_batch(engine, lead.id)

    write_while_batch_is_open(engine, batch, lambda r: r.set_reply_status(
        reply.id, ReplyStatus.FAILED, now=NOW, error="smtp rejected anna@example.com",
    ))

    session.expire_all()
    stored = session.get(Reply, reply.id)
    assert stored.status == "failed"
    assert stored.error is None


# For a normal lead the error is stored as before.
def test_error_text_is_stored_for_a_normal_lead(session):
    repo = Repository(session)
    lead = make_lead(repo, NOW - timedelta(days=1))
    reply = add_reply(repo, lead.id, status=ReplyStatus.DRAFTED)
    repo.set_reply_status(reply.id, ReplyStatus.FAILED, now=NOW, error="timeout: may have been sent")
    session.expire_all()
    assert session.get(Reply, reply.id).error == "timeout: may have been sent"


# Two retention jobs started at the same moment (an overlapping schedule, a manual run during the
# nightly one). Each run uses its own `now`, so we can see WHICH run anonymized each lead:
# every lead must be handled exactly once (counts add up, no timestamp overwritten by the other run).
def test_two_retention_runs_at_once_handle_each_lead_exactly_once(engine, session):
    repo = Repository(session)
    for i in range(30):
        add_reply(repo, make_lead(repo, CUTOFF - timedelta(days=1), email=f"p{i}@example.com").id)
    barrier = threading.Barrier(2)
    run_times = [NOW, NOW + timedelta(hours=1)]

    def run(now):
        with Session(engine, expire_on_commit=False) as s:
            barrier.wait()
            return Repository(s).anonymize_older_than(cutoff=CUTOFF, now=now, batch_size=4)

    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(run, run_times))

    assert sum(counts) == 30
    session.expire_all()
    stamps = [lead.anonymized_at for lead in session.scalars(select(Lead))]
    assert all(stamp in run_times for stamp in stamps)
    assert stamps.count(run_times[0]) == counts[0]
    assert stamps.count(run_times[1]) == counts[1]
    assert all(r.body == "[anonymized]" for r in session.scalars(select(Reply)))


# The ids of one batch go into a single IN (...) list; Postgres allows 65535 bind parameters in a
# statement, so a bigger batch_size would crash EVERY run without anonymizing anything.
def test_batch_size_above_the_bind_parameter_limit_is_rejected(session):
    with pytest.raises(ValueError):
        Repository(session).anonymize_older_than(cutoff=CUTOFF, now=NOW, batch_size=60_001)
