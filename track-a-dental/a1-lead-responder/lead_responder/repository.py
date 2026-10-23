"""Repository: the only code that reads and writes leads and replies (staff and audit come later).

Why a repository: the rest of the app asks for "create this lead" or "set this status" and
never builds queries itself. Validation, duplicate detection and transaction handling live in
one tested place. Each public method is ONE transaction (except `anonymize_older_than`, which commits once per batch): it commits on success and rolls the
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

# Width of leads.language, read from the model so the two cannot drift apart.
LANGUAGE_MAX = Lead.__table__.c.language.type.length

# Retention handles this many leads per transaction: short locks, and no giant IN (...) list
# (Postgres allows at most 65535 bind parameters in one statement).
RETENTION_BATCH_SIZE = 5000


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


# Fingerprint of "same person, same message": lower-cased email + NUL + stripped message.
# The separator must be a character that cannot occur in either input. _clean() removes NUL
# from all text, so callers MUST pass cleaned values (create_lead_if_new does, before this call).
# With a newline as separator, ("x@y.de<newline>A", "Q") and ("x@y.de", "A<newline>Q") collided
# and the second, different patient was dropped as a duplicate.
def _dedup_key(email: str, message: str) -> str:
    return hashlib.sha256(f"{email.lower()}{chr(0)}{message.strip()}".encode()).hexdigest()


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
    # Refuses an anonymized lead: new text about the patient would never be cleaned again.
    def set_classification(
        self, lead_id: int, *, classification: Classification | None, decision: Decision,
    ) -> Lead:
        try:
            lead = self._require(lead_id)
            self._refuse_if_anonymized(lead_id)
            if classification is not None:
                lead.category = classification.category.value
                lead.risk = classification.risk.value
                lead.urgency = classification.urgency.value
                # The model's text is untrusted: cut the language to the column width and drop NUL
                # from the reason, otherwise the whole update fails and the decision flags are lost.
                # Clean first, then cut: NUL inside the language would otherwise survive the cut.
                lead.language = _clean(classification.language)[:LANGUAGE_MAX]
                lead.ai_reason = _clean(classification.reason)
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
    # Refuses an anonymized lead (ValueError): the reply text would be personal data that
    # retention no longer looks for.
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
            self._refuse_if_anonymized(lead_id)
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
    # a lead exactly at the cutoff stays) that is not anonymized yet. Reply texts, reply errors
    # (they may hold a recipient address), the dedup_key hash and ai_reason (free text about the
    # patient) count as personal data too. Category, risk, urgency, language, statuses and
    # timestamps stay, so the statistics survive. Audit rows are not touched (they only point
    # at ids).
    # Work is done in batches of `batch_size` leads, each batch ONE short transaction (select the
    # next ids FOR UPDATE, blank their replies and the leads, commit), until none are left. This
    # avoids a giant IN list and long locks. A crash mid-run is safe: finished batches stay
    # done, the `anonymized_at IS NULL` filter makes a re-run continue; a second run returns 0 (it only
    # re-runs the sweep below, which blanks leftovers).
    # Finally a SWEEP blanks leftovers on leads that are ALREADY anonymized (a reply or
    # ai_reason that slipped in later, e.g. a send error written after anonymization); the
    # sweep does not change the return value. Returns how many leads were newly anonymized.
    def anonymize_older_than(
        self, *, cutoff: datetime, now: datetime, batch_size: int = RETENTION_BATCH_SIZE,
    ) -> int:
        _require_aware("cutoff", cutoff)
        _require_aware("now", now)
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        total = 0
        try:
            while True:
                # FOR UPDATE: a concurrent run waits here, then finds nothing left to do.
                ids = list(self.session.scalars(
                    select(Lead.id)
                    .where(Lead.created_at < cutoff, Lead.anonymized_at.is_(None))
                    .order_by(Lead.id)
                    .limit(batch_size)
                    .with_for_update()
                ))
                if not ids:
                    break
                self.session.execute(
                    update(Reply).where(Reply.lead_id.in_(ids)).values(body="[anonymized]", ai_body=None, error=None)
                )
                self.session.execute(
                    update(Lead).where(Lead.id.in_(ids)).values(
                        name="[anonymized]", email="", phone=None, message="",
                        dedup_key=None, ai_reason=None, anonymized_at=now,
                    )
                )
                self.session.commit()
                total += len(ids)
            # Sweep: set-based UPDATEs with a subquery (no id list), one transaction.
            done = select(Lead.id).where(Lead.anonymized_at.is_not(None))
            self.session.execute(
                update(Reply)
                .where(
                    Reply.lead_id.in_(done),
                    (Reply.body != "[anonymized]") | Reply.ai_body.is_not(None) | Reply.error.is_not(None),
                )
                .values(body="[anonymized]", ai_body=None, error=None)
            )
            self.session.execute(
                update(Lead)
                .where(Lead.anonymized_at.is_not(None), Lead.ai_reason.is_not(None))
                .values(ai_reason=None)
            )
            self.session.commit()
            # Loaded Lead/Reply objects in this session are now stale; drop them so callers re-read.
            self.session.expire_all()
            return total
        except BaseException:
            self.session.rollback()
            raise

    # Raises ValueError for an anonymized lead. Reads the column straight from the database
    # (not the identity map) inside the caller's transaction, so a cached object cannot hide it.
    def _refuse_if_anonymized(self, lead_id: int) -> None:
        anonymized_at = self.session.scalar(select(Lead.anonymized_at).where(Lead.id == lead_id))
        if anonymized_at is not None:
            raise ValueError(f"lead {lead_id} is anonymized")

    def _require(self, lead_id: int) -> Lead:
        lead = self.session.get(Lead, lead_id)
        if lead is None:
            raise LookupError(f"lead {lead_id} not found")
        return lead
