"""Repository: the only code that reads and writes leads and replies (staff and audit come later).

Why a repository: the rest of the app asks for "create this lead" or "set this status" and
never builds queries itself. Validation, duplicate detection and transaction handling live in
one tested place. Each public method is ONE transaction: it commits on success and rolls the
session back on any failure, so the caller's Session stays usable.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from lead_responder.db.models import Lead, LeadStatus, Reply, ReplyStatus
from lead_responder.models import Classification
from lead_responder.router import Action, Decision

DUPLICATE_WINDOW = timedelta(minutes=2)

# Same limits as the CHECK constraints in db/models.py. We check here first so the caller
# gets a clear ValueError; the CHECKs stay as the last line of defence.
MAX_NAME, MAX_EMAIL, MAX_PHONE, MAX_MESSAGE = 200, 254, 50, 10_000


# Postgres text columns cannot hold NUL (\x00); drop it instead of crashing the insert.
def _clean(value: str) -> str:
    return value.replace(chr(0), "")


# Length is checked AFTER cleaning, because that is what would be stored.
def _check_len(field: str, value: str, limit: int) -> None:
    if len(value) > limit:
        raise ValueError(f"{field} is too long ({len(value)} > {limit} characters)")


# We only accept timezone-aware datetimes: a naive one would silently mean "server local time".
def _require_aware(field: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


# Fingerprint of "same person, same message": lower-cased email + newline + stripped message.
def _dedup_key(email: str, message: str) -> str:
    return hashlib.sha256(f"{email.lower()}\n{message.strip()}".encode()).hexdigest()


# Postgres advisory locks take a signed 64-bit integer: use the first 8 bytes of the hash.
def _lock_id(dedup_key: str) -> int:
    return int.from_bytes(bytes.fromhex(dedup_key[:16]), "big", signed=True)


class Repository:
    def __init__(self, session: Session):
        self.session = session

    # Creates a lead unless the same person sent the same message in the last 2 minutes.
    # The advisory lock makes "look, then insert" atomic: two simultaneous identical
    # submissions queue up on the lock, so the second one sees the first one's row.
    # The lock is released automatically at commit/rollback.
    def create_lead_if_new(
        self, *, name: str, email: str, phone: str | None, message: str,
        consent_at: datetime, now: datetime, channel: str = "web_form",
    ) -> tuple[Lead, bool]:
        _require_aware("now", now)
        _require_aware("consent_at", consent_at)
        name, email, message = _clean(name), _clean(email), _clean(message)
        phone = _clean(phone) if phone is not None else None
        _check_len("name", name, MAX_NAME)
        _check_len("email", email, MAX_EMAIL)
        _check_len("message", message, MAX_MESSAGE)
        if phone is not None:
            _check_len("phone", phone, MAX_PHONE)

        key = _dedup_key(email, message)
        try:
            self.session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _lock_id(key)})
            # Deliberately no upper bound on created_at: a lead timestamped later than `now` still counts.
            existing = self.session.scalar(
                select(Lead)
                .where(Lead.dedup_key == key, Lead.created_at >= now - DUPLICATE_WINDOW)
                .order_by(Lead.id)
                .limit(1)
            )
            if existing is not None:
                self.session.commit()  # releases the lock
                return existing, False
            lead = Lead(
                channel=channel, name=name, email=email, phone=phone, message=message,
                consent_at=consent_at, created_at=now, status=LeadStatus.NEW.value, dedup_key=key,
            )
            self.session.add(lead)
            self.session.commit()
            return lead, True
        except BaseException:
            self.session.rollback()
            raise

    def get_lead(self, lead_id: int) -> Lead | None:
        return self.session.get(Lead, lead_id)

    # Stores the classifier's result and the router's flags. The classification can be None
    # (the AI failed); the flags from the decision are then still saved so staff see the lead.
    def set_classification(
        self, lead_id: int, *, classification: Classification | None, decision: Decision,
    ) -> Lead:
        try:
            lead = self._require(lead_id)
            if classification is not None:
                lead.category = classification.category.value
                lead.risk = classification.risk.value
                lead.urgency = classification.urgency.value
                lead.language = classification.language
                lead.ai_reason = classification.reason
            lead.language_unsupported = decision.language_unsupported
            lead.needs_manual_review = decision.needs_manual_review
            self.session.commit()
            return lead
        except BaseException:
            self.session.rollback()
            raise

    # Only a LeadStatus is accepted: a typo like "replyed" fails here with a clear message.
    def set_lead_status(self, lead_id: int, status: LeadStatus) -> Lead:
        if not isinstance(status, LeadStatus):
            raise ValueError(f"status must be a LeadStatus, got {status!r}")
        try:
            lead = self._require(lead_id)
            lead.status = status.value
            self.session.commit()
            return lead
        except BaseException:
            self.session.rollback()
            raise

    # Stores one reply (what the AI wrote in ai_body, what will actually be sent in body).
    # kind and status must be enum members; a missing lead fails on the foreign key
    # (IntegrityError) and the session is rolled back so the caller can carry on.
    def add_reply(
        self, lead_id: int, *, kind: Action, ai_body: str | None, body: str,
        status: ReplyStatus, now: datetime, edited: bool | None = None,
    ) -> Reply:
        if not isinstance(kind, Action):
            raise ValueError(f"kind must be an Action, got {kind!r}")
        if not isinstance(status, ReplyStatus):
            raise ValueError(f"status must be a ReplyStatus, got {status!r}")
        _require_aware("now", now)
        try:
            reply = Reply(
                lead_id=lead_id, kind=kind.value,
                ai_body=_clean(ai_body) if ai_body is not None else None,
                body=_clean(body), status=status.value, created_at=now, edited=edited,
                # A reply created already "sent" needs its timestamp too.
                sent_at=now if status is ReplyStatus.SENT else None,
            )
            self.session.add(reply)
            self.session.commit()
            return reply
        except BaseException:
            self.session.rollback()
            raise

    # Moves a reply to a new status. sent_at is set only when the new status is "sent".
    # `error` is assigned every time (None clears it: a successful resend must not keep an old
    # error); approved_by and edited are only changed when given, so later steps do not wipe them.
    def set_reply_status(
        self, reply_id: int, status: ReplyStatus, *, now: datetime, error: str | None = None,
        approved_by: int | None = None, edited: bool | None = None,
    ) -> Reply:
        if not isinstance(status, ReplyStatus):
            raise ValueError(f"status must be a ReplyStatus, got {status!r}")
        _require_aware("now", now)
        try:
            reply = self.session.get(Reply, reply_id)
            if reply is None:
                raise LookupError(f"reply {reply_id} not found")
            reply.status = status.value
            reply.error = _clean(error) if error is not None else None
            if status is ReplyStatus.SENT:
                reply.sent_at = now
            if approved_by is not None:
                reply.approved_by = approved_by
            if edited is not None:
                reply.edited = edited
            self.session.commit()
            return reply
        except BaseException:
            self.session.rollback()
            raise

    # All replies of one lead, oldest first (id breaks ties between equal timestamps).
    def replies_for(self, lead_id: int) -> list[Reply]:
        return list(self.session.scalars(
            select(Reply).where(Reply.lead_id == lead_id).order_by(Reply.created_at, Reply.id)
        ))

    # DSGVO retention: blank the personal data of every lead created BEFORE `cutoff` (strictly:
    # a lead exactly at the cutoff stays) that is not anonymized yet. Reply texts and the
    # dedup_key hash count as personal data too. Category, risk, urgency, language, statuses and
    # timestamps stay, so the statistics survive. Audit rows are not touched (they only point
    # at ids). Leads and replies change in ONE transaction; the `anonymized_at IS NULL` filter
    # makes a second run a no-op. Returns how many leads were changed.
    def anonymize_older_than(self, *, cutoff: datetime, now: datetime) -> int:
        _require_aware("cutoff", cutoff)
        _require_aware("now", now)
        try:
            # FOR UPDATE: a concurrent run waits here, then finds nothing left to do.
            ids = list(self.session.scalars(
                select(Lead.id)
                .where(Lead.created_at < cutoff, Lead.anonymized_at.is_(None))
                .with_for_update()
            ))
            if ids:
                self.session.execute(
                    update(Reply).where(Reply.lead_id.in_(ids)).values(body="[anonymized]", ai_body=None)
                )
                self.session.execute(
                    update(Lead).where(Lead.id.in_(ids)).values(
                        name="[anonymized]", email="", phone=None, message="",
                        dedup_key=None, anonymized_at=now,
                    )
                )
            self.session.commit()
            # Loaded Lead/Reply objects in this session are now stale; drop them so callers re-read.
            self.session.expire_all()
            return len(ids)
        except BaseException:
            self.session.rollback()
            raise

    def _require(self, lead_id: int) -> Lead:
        lead = self.session.get(Lead, lead_id)
        if lead is None:
            raise LookupError(f"lead {lead_id} not found")
        return lead
