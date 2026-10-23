"""Retention (DSGVO) tests: personal data blanked, statistics kept, exact boundary, idempotent."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

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


def add_reply(repo: Repository, lead_id: int, n: int = 1):
    return repo.add_reply(
        lead_id, kind=Action.DRAFT, ai_body=f"AI text {n}", body=f"Edited text {n}",
        status=ReplyStatus.SENT, now=NOW, edited=True,
    )


def test_personal_fields_are_blanked_and_statistics_kept(session):
    repo = Repository(session)
    lead = make_lead(repo, CUTOFF - timedelta(days=1))
    lead.category, lead.risk, lead.urgency, lead.language = "appointment", "low", "normal", "de"
    lead.ai_reason = "Patient nennt Zahnschmerzen"  # free text about the patient: must go
    session.commit()
    repo.set_lead_status(lead.id, LeadStatus.REPLIED)

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
    r1, r2 = add_reply(repo, lead.id, 1), add_reply(repo, lead.id, 2)
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
    assert len(commits) >= 3  # batches of 3, 3, 1 (plus the sweep)


def test_batch_size_must_be_positive(session):
    with pytest.raises(ValueError):
        Repository(session).anonymize_older_than(cutoff=CUTOFF, now=NOW, batch_size=0)


# F2 (a): personal data that re-enters an already anonymized lead (written around the guard)
# is swept away on the next run, although the lead itself is not "newly anonymized".
def test_sweep_blanks_leftovers_on_already_anonymized_leads(session):
    repo = Repository(session)
    lead = make_lead(repo, CUTOFF - timedelta(days=1))
    assert repo.anonymize_older_than(cutoff=CUTOFF, now=NOW) == 1
    # bypass the repository guard, as a bug or a raw SQL fix would
    lead_row = session.get(Lead, lead.id)
    lead_row.ai_reason = "Patient Anna hat Angst"
    late = Reply(
        lead_id=lead.id, kind="draft", ai_body="Dear Anna", body="Dear Anna",
        status="failed", error="smtp rejected anna@example.com", created_at=NOW,
    )
    session.add(late)
    session.commit()

    assert repo.anonymize_older_than(cutoff=CUTOFF, now=NOW + timedelta(days=1)) == 0

    session.expire_all()
    got = session.get(Lead, lead.id)
    assert got.ai_reason is None
    assert got.anonymized_at == NOW  # not moved
    rep = session.get(Reply, late.id)
    assert (rep.body, rep.ai_body, rep.error) == ("[anonymized]", None, None)
    assert rep.status == "failed"  # statistics kept


# set_reply_status stays allowed on an anonymized lead's reply; its error is swept next run.
def test_set_reply_status_error_on_anonymized_lead_is_swept(session):
    repo = Repository(session)
    lead = make_lead(repo, CUTOFF - timedelta(days=1))
    reply = add_reply(repo, lead.id)
    repo.anonymize_older_than(cutoff=CUTOFF, now=NOW)
    repo.set_reply_status(reply.id, ReplyStatus.FAILED, now=NOW, error="smtp rejected x@y.de")
    repo.anonymize_older_than(cutoff=CUTOFF, now=NOW)
    session.expire_all()
    assert session.get(Reply, reply.id).error is None
