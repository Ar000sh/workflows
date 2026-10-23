"""Schema tests: migrations create the tables, models match migrations, the DB enforces the rules."""
from datetime import UTC, datetime

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from psycopg.errors import CheckViolation
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from lead_responder.db.models import AuditLog, Base, Lead, Reply, StaffUser


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
