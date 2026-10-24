"""Schema tests: migrations create the tables, models match migrations, the DB enforces the rules."""
from datetime import UTC, datetime

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from psycopg.errors import CheckViolation
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from lead_responder.db.models import (
    AUDIT_ACTIONS, REPLY_KINDS, AuditLog, Base, Lead, LeadStatus, Reply, ReplyStatus, StaffUser,
)


def make_lead(**overrides) -> Lead:
    fields = dict(
        channel="web_form", name="Anna Test", email="anna@example.com",
        message="Ich moechte einen Termin.", consent_at=datetime.now(UTC), status="new",
    )
    fields.update(overrides)
    return Lead(**fields)


# The migration must create the four spec tables (plus Alembic's own bookkeeping table).
def test_upgrade_creates_all_tables(engine):
    tables = set(inspect(engine).get_table_names())
    assert {"leads", "replies", "staff_users", "audit_log", "alembic_version"} <= tables


# Drift guard: if someone edits a model and forgets the migration, the diff is not empty.
def test_models_and_migration_agree(engine):
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


# The database itself rejects unknown status/kind/action strings, not only our Python code.
def test_check_constraints_reject_unknown_values(session):
    def expect_check_violation(obj):
        session.add(obj)
        with pytest.raises(IntegrityError) as exc:
            session.flush()
        assert isinstance(exc.value.orig, CheckViolation)
        session.rollback()

    expect_check_violation(make_lead(status="banana"))

    lead = make_lead()
    session.add(lead)
    session.commit()
    expect_check_violation(Reply(lead_id=lead.id, kind="banana", ai_body="x", body="x", status="drafted"))
    expect_check_violation(Reply(lead_id=lead.id, kind="draft", ai_body="x", body="x", status="banana"))
    expect_check_violation(AuditLog(action="banana", lead_id=lead.id))


# Consent is mandatory (DSGVO) and field lengths are capped in the database.
def test_lead_requires_consent_and_limits_lengths(session):
    for lead in (make_lead(consent_at=None), make_lead(name="a" * 201), make_lead(message="a" * 10_001)):
        session.add(lead)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    session.add(make_lead(name="a" * 200, message="a" * 10_000))
    session.commit()


# StaffUser is imported so a typo in the model name fails loudly; a row round-trips.
def test_staff_user_roundtrip(session):
    session.add(StaffUser(name="Dr. Test", email="dr@example.com", password_hash="x"))
    session.commit()
    assert session.query(StaffUser).count() == 1


# compare_metadata does NOT diff CHECK constraints, so test_models_and_migration_agree cannot see a new
# status/kind/action that was added to the models but not to a migration. This positive test can: it
# inserts one row per allowed value, so a missing migration makes the DB reject the value right here.
def test_every_allowed_value_is_accepted_by_the_database(session):
    for status in LeadStatus:
        session.add(make_lead(status=status.value))
    session.commit()

    lead = make_lead()
    session.add(lead)
    session.commit()
    for status in ReplyStatus:
        session.add(Reply(lead_id=lead.id, kind="draft", ai_body="x", body="x", status=status.value))
    for kind in REPLY_KINDS:
        session.add(Reply(lead_id=lead.id, kind=kind, ai_body="x", body="x", status="drafted"))
    for action in AUDIT_ACTIONS:
        session.add(AuditLog(action=action, lead_id=lead.id))
    session.commit()


# --- The "sending claim" revision: a migration with data in the tables, and its way back. ---
# These tests move the shared test database between revisions and always put it back to head.

INIT_REVISION = "a30306790587"
OLD_REPLY_STATUSES = ("drafted", "approved", "sent", "rejected", "failed")


# Run `body` with the schema at `revision`, then restore head (even if the body fails), so a failed
# migration test cannot leave the other DB tests on an old schema.
def at_revision(engine, cfg, revision, body):
    from alembic import command

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE leads, replies, staff_users, audit_log RESTART IDENTITY CASCADE"))
    try:
        command.downgrade(cfg, revision)
        body()
    finally:
        command.upgrade(cfg, "head")


# Real data from before the revision must survive the upgrade, with the new columns at their defaults.
def test_migration_upgrade_keeps_existing_rows(engine, alembic_cfg):
    from alembic import command

    def body():
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO leads (channel, name, email, message, consent_at, status) "
                "VALUES ('web_form', 'Anna', 'a@example.com', 'Hallo', now(), 'new')"
            ))
            for status in OLD_REPLY_STATUSES:
                conn.execute(
                    text("INSERT INTO replies (lead_id, kind, body, status) VALUES (1, 'draft', 'x', :s)"),
                    {"s": status},
                )
        command.upgrade(alembic_cfg, "head")
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT status, send_attempts, claimed_at FROM replies ORDER BY id")).all()
            lead = conn.execute(text("SELECT send_suppressed, mailbox_key FROM leads")).one()
        assert [r.status for r in rows] == list(OLD_REPLY_STATUSES)
        assert all(r.send_attempts == 0 and r.claimed_at is None for r in rows)
        assert lead.send_suppressed is False and lead.mailbox_key is None

    at_revision(engine, alembic_cfg, INIT_REVISION, body)


# Going back must not fail on a row the old CHECK would refuse: "sending" (outcome unknown) becomes
# "failed" (the closest old status), the new columns go, and the old status list is back.
def test_migration_downgrade_maps_sending_to_failed(engine, alembic_cfg):
    from alembic import command

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE leads, replies, staff_users, audit_log RESTART IDENTITY CASCADE"))
        conn.execute(text(
            "INSERT INTO leads (channel, name, email, message, consent_at, status) "
            "VALUES ('web_form', 'Anna', 'a@example.com', 'Hallo', now(), 'new')"
        ))
        conn.execute(text("INSERT INTO replies (lead_id, kind, body, status) VALUES (1, 'draft', 'x', 'sending')"))
    try:
        command.downgrade(alembic_cfg, INIT_REVISION)
        insp = inspect(engine)
        reply_columns = {c["name"] for c in insp.get_columns("replies")}
        lead_columns = {c["name"] for c in insp.get_columns("leads")}
        assert not reply_columns & {"claimed_at", "send_attempts"}
        assert not lead_columns & {"send_suppressed", "mailbox_key"}
        with engine.connect() as conn:
            assert conn.execute(text("SELECT status FROM replies")).scalar_one() == "failed"
        with engine.begin() as conn, pytest.raises(IntegrityError):
            conn.execute(text("INSERT INTO replies (lead_id, kind, body, status) VALUES (1, 'draft', 'x', 'sending')"))
    finally:
        command.upgrade(alembic_cfg, "head")


# The new columns' own rules: attempts cannot go negative, and "sending" is a legal status.
def test_new_columns_have_database_rules(session):
    lead = make_lead()
    session.add(lead)
    session.commit()
    session.add(Reply(lead_id=lead.id, kind="draft", body="x", status="drafted", send_attempts=-1))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
