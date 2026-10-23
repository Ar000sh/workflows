"""Leads repository tests: duplicate detection, hostile text, validation, status updates."""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from lead_responder.db.models import Lead, LeadStatus
from lead_responder.models import Category, Classification, Risk, Urgency
from lead_responder.repository import Repository, _dedup_key, _lock_id
from lead_responder.router import Action, Decision

NOW = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)


def payload(**overrides) -> dict:
    fields = dict(
        name="Anna Test", email="anna@example.com", phone="0511 123456",
        message="Ich moechte einen Termin.", consent_at=NOW, now=NOW,
    )
    fields.update(overrides)
    return fields


def count_leads(session) -> int:
    return session.scalar(select(func.count()).select_from(Lead))


def test_create_lead_returns_new_lead_with_status_new(session):
    lead, created = Repository(session).create_lead_if_new(**payload())
    assert created is True
    assert lead.id is not None
    assert lead.status == "new"
    assert lead.channel == "web_form"
    assert lead.created_at == NOW
    assert lead.dedup_key and len(lead.dedup_key) == 64


def test_duplicate_within_two_minutes_returns_existing_and_false(session):
    repo = Repository(session)
    first, _ = repo.create_lead_if_new(**payload())
    second, created = repo.create_lead_if_new(
        **payload(email="ANNA@Example.com", message="  Ich moechte einen Termin.\n", now=NOW + timedelta(seconds=119))
    )
    assert created is False
    assert second.id == first.id
    assert count_leads(session) == 1


def test_same_message_after_three_minutes_is_a_new_lead(session):
    repo = Repository(session)
    first, _ = repo.create_lead_if_new(**payload())
    second, created = repo.create_lead_if_new(**payload(now=NOW + timedelta(minutes=3)))
    assert created is True
    assert second.id != first.id


def test_different_message_is_a_new_lead(session):
    repo = Repository(session)
    repo.create_lead_if_new(**payload())
    _, created = repo.create_lead_if_new(**payload(message="Etwas ganz anderes."))
    assert created is True
    assert count_leads(session) == 2


def test_two_sessions_racing_create_exactly_one_lead(engine, session):
    # Barrier makes both threads start together; each has its own Session (= own connection).
    barrier = threading.Barrier(2)

    def submit(_):
        with Session(engine, expire_on_commit=False) as s:
            barrier.wait(timeout=5)
            return Repository(s).create_lead_if_new(**payload())[1]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert sorted(results) == [False, True]
    assert count_leads(session) == 1


# Deterministic proof that the advisory lock is what serialises: the test itself holds the lock,
# so the worker MUST block until we commit. Without the lock line the worker would finish at once.
def test_create_blocks_on_the_advisory_lock_until_holder_commits(engine, session):
    p = payload()
    key = _dedup_key(p["email"], p["message"])
    result = {}

    def worker():
        with Session(engine, expire_on_commit=False) as s:
            result["created"] = Repository(s).create_lead_if_new(**p)[1]

    with Session(engine, expire_on_commit=False) as holder:
        holder.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _lock_id(key)})
        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=0.3)
        assert t.is_alive(), "worker did not wait for the advisory lock"
        # The holder inserts the matching lead, then commits (releasing the lock).
        holder.add(Lead(
            name=p["name"], email=p["email"], message=p["message"], consent_at=NOW,
            created_at=NOW, status="new", dedup_key=key,
        ))
        holder.commit()
    t.join(timeout=5)
    assert not t.is_alive()
    assert result["created"] is False  # saw the holder's committed row
    assert count_leads(session) == 1


def test_nul_bytes_are_stripped(session):
    lead, _ = Repository(session).create_lead_if_new(**payload(name="a\x00b", message="hi\x00 there"))
    assert lead.name == "ab"
    assert lead.message == "hi there"


def test_sql_metacharacters_are_stored_literally(session):
    evil = "Robert'); DROP TABLE leads;--"
    lead, _ = Repository(session).create_lead_if_new(**payload(name=evil, message=evil))
    session.expire_all()
    assert session.get(Lead, lead.id).name == evil
    assert session.scalar(text("SELECT count(*) FROM leads")) == 1


def test_emoji_and_long_message_roundtrip(session):
    repo = Repository(session)
    ok = "ä😀" * 5000  # exactly 10,000 characters
    lead, _ = repo.create_lead_if_new(**payload(message=ok))
    session.expire_all()
    assert session.get(Lead, lead.id).message == ok
    with pytest.raises(ValueError):
        repo.create_lead_if_new(**payload(message=ok + "x"))
    assert count_leads(session) == 1


@pytest.mark.parametrize("field,limit", [("name", 200), ("email", 254), ("phone", 50)])
def test_overlong_fields_raise_value_error(session, field, limit):
    with pytest.raises(ValueError):
        Repository(session).create_lead_if_new(**payload(**{field: "x" * (limit + 1)}))
    assert count_leads(session) == 0


def test_naive_datetimes_are_rejected(session):
    repo = Repository(session)
    naive = datetime(2026, 10, 23, 10, 0)
    with pytest.raises(ValueError):
        repo.create_lead_if_new(**payload(now=naive))
    with pytest.raises(ValueError):
        repo.create_lead_if_new(**payload(consent_at=naive))


def test_session_stays_usable_after_a_database_error(session):
    repo = Repository(session)
    lead, _ = repo.create_lead_if_new(**payload())
    with pytest.raises(Exception):
        repo.create_lead_if_new(**payload(message="x", channel="y" * 31))  # exceeds String(30)
    assert repo.get_lead(lead.id) is not None


def test_get_lead_returns_none_for_unknown_id(session):
    assert Repository(session).get_lead(999) is None


def test_set_classification_stores_fields_and_decision_flags(session):
    repo = Repository(session)
    lead, _ = repo.create_lead_if_new(**payload())
    cls = Classification(
        category=Category.APPOINTMENT, risk=Risk.ROUTINE, urgency=Urgency.NORMAL,
        language="de", reason="Terminwunsch",
    )
    decision = Decision(
        risk=Risk.ROUTINE, actions=(Action.AUTO_REPLY,), language="de", emergency=False,
        language_unsupported=False, needs_manual_review=False,
    )
    updated = repo.set_classification(lead.id, classification=cls, decision=decision)
    assert (updated.category, updated.risk, updated.urgency, updated.language) == (
        "appointment", "routine", "normal", "de",
    )
    assert updated.ai_reason == "Terminwunsch"


def test_set_classification_with_none_keeps_decision_flags(session):
    repo = Repository(session)
    lead, _ = repo.create_lead_if_new(**payload())
    decision = Decision(
        risk=Risk.SENSITIVE, actions=(Action.ACKNOWLEDGMENT,), language="de+en", emergency=False,
        language_unsupported=True, needs_manual_review=True,
    )
    updated = repo.set_classification(lead.id, classification=None, decision=decision)
    assert updated.language_unsupported is True
    assert updated.needs_manual_review is True
    assert updated.category is None


def test_set_lead_status_updates_and_persists(session):
    repo = Repository(session)
    lead, _ = repo.create_lead_if_new(**payload())
    repo.set_lead_status(lead.id, LeadStatus.CLASSIFIED)
    session.expire_all()
    assert session.get(Lead, lead.id).status == "classified"


def test_set_lead_status_rejects_a_plain_string(session):
    repo = Repository(session)
    lead, _ = repo.create_lead_if_new(**payload())
    with pytest.raises(ValueError):
        repo.set_lead_status(lead.id, "bogus")
    with pytest.raises(ValueError):
        repo.set_lead_status(lead.id, "done")  # even a valid value must be the enum


def test_exactly_120_seconds_is_a_duplicate_and_121_is_new(session):
    repo = Repository(session)
    first, _ = repo.create_lead_if_new(**payload())
    same, created = repo.create_lead_if_new(**payload(now=NOW + timedelta(seconds=120)))
    assert created is False and same.id == first.id
    _, created = repo.create_lead_if_new(**payload(now=NOW + timedelta(seconds=121)))
    assert created is True


def test_unknown_lead_id_raises_lookup_error(session):
    repo = Repository(session)
    decision = Decision(
        risk=Risk.ROUTINE, actions=(Action.AUTO_REPLY,), language="de", emergency=False,
        language_unsupported=False, needs_manual_review=False,
    )
    with pytest.raises(LookupError):
        repo.set_lead_status(999, LeadStatus.DONE)
    with pytest.raises(LookupError):
        repo.set_classification(999, classification=None, decision=decision)
