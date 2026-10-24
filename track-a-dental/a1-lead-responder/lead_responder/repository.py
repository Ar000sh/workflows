"""Repository: the only code that reads and writes leads and replies (staff and audit come later).

Why a repository: the rest of the app asks for "create this lead" or "set this status" and
never builds queries itself. Validation, duplicate detection and transaction handling live in
one tested place. Each public method is ONE transaction (except `anonymize_older_than`, which commits once per batch): it commits on success and rolls the
session back on any failure, so the caller's Session stays usable.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from lead_responder.db.models import (
    LEAD_TRANSITIONS, REPLY_TRANSITIONS, Lead, LeadStatus, Reply, ReplyStatus,
)
from lead_responder.models import Classification
from lead_responder.router import Action, Decision

DUPLICATE_WINDOW = timedelta(minutes=2)

# The reply kinds that count as a lead's "first reply": a lead gets at most one of them.
# A staff draft is not one (it comes later and needs approval).
FIRST_REPLY_KINDS = (Action.ACKNOWLEDGMENT, Action.AUTO_REPLY)
# Lead statuses that are still waiting for their first reply (acknowledge_lead and the sweep).
AWAITING_FIRST_REPLY = (LeadStatus.NEW, LeadStatus.CLASSIFIED, LeadStatus.ACKNOWLEDGED)
# suppress_lead's only allowed move: a narrower table than LEAD_TRANSITIONS (which also allows
# classified -> acknowledged), so _move_status checks "status is new" in its one UPDATE.
SUPPRESS_TRANSITIONS = {LeadStatus.NEW: frozenset({LeadStatus.ACKNOWLEDGED})}

# Width of leads.language, read from the model so the two cannot drift apart.
LANGUAGE_MAX = Lead.__table__.c.language.type.length

# Retention handles this many leads per transaction: short locks, short IN (...) lists.
RETENTION_BATCH_SIZE = 5000
# Postgres allows 65535 bind parameters per statement and the batch's ids are one IN list, so a
# bigger batch would fail on every run. Capped below the limit, leaving room for the other values.
MAX_RETENTION_BATCH_SIZE = 60_000


# Postgres text columns cannot hold NUL (\x00); drop it instead of crashing the insert.
def _clean(value: str) -> str:
    return value.replace(chr(0), "")


# We only accept timezone-aware datetimes: a naive one would silently mean "server local time".
def _require_aware(field: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


# Fingerprint of "same person, same message": lower-cased email + NUL + stripped message.
# The separator must be a character that cannot occur in either input. LeadIn (api/schemas.py)
# removes NUL from all form text, so callers MUST pass LeadIn-cleaned values.
# With a newline as separator, ("x@y.de<newline>A", "Q") and ("x@y.de", "A<newline>Q") collided
# and the second, different patient was dropped as a duplicate.
def _dedup_key(email: str, message: str) -> str:
    return hashlib.sha256(f"{email.lower()}{chr(0)}{message.strip()}".encode()).hexdigest()


# Postgres advisory locks take a signed 64-bit integer: use the first 8 bytes of the hash.
def _lock_id(dedup_key: str) -> int:
    return int.from_bytes(bytes.fromhex(dedup_key[:16]), "big", signed=True)


# A ValueError subclass: the status change is not allowed from the current status (or another
# session changed the row first). Callers that already catch ValueError keep working.
class InvalidTransition(ValueError):
    pass


class Repository:
    def __init__(self, session: Session):
        self.session = session

    # Creates a lead unless the same person sent the same message in the last 2 minutes.
    # The advisory lock makes "look, then insert" atomic: two simultaneous identical
    # submissions queue up on the lock, so the second one sees the first one's row.
    # The lock is released automatically at commit/rollback. The fields must already be validated
    # and cleaned by LeadIn; the database CHECKs reject anything that slipped through.
    # mailbox_key (limits.mailbox_key of the email) is stored so the per-address limit can count it.
    def create_lead_if_new(
        self, *, name: str, email: str, phone: str | None, message: str,
        consent_at: datetime, now: datetime, mailbox_key: str, channel: str = "web_form",
    ) -> tuple[Lead, bool]:
        _require_aware("now", now)
        _require_aware("consent_at", consent_at)
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

            # was not found and therefor has to get created
            lead = Lead(
                channel=channel, name=name, email=email, phone=phone, message=message,
                consent_at=consent_at, created_at=now, status=LeadStatus.NEW.value, dedup_key=key,
                mailbox_key=mailbox_key,
            )
            self.session.add(lead)
            self.session.commit()
            return lead, True
        except BaseException:
            self.session.rollback()
            raise

    # Reads end their transaction too (commit on success, rollback on error), so a failed read
    # cannot break the next write and no session sits "idle in transaction". populate_existing
    # reloads from the database, so a long-lived session never shows rows another session changed.
    def get_lead(self, lead_id: int) -> Lead | None:
        try:
            lead = self.session.get(Lead, lead_id, populate_existing=True)
            self.session.commit()
            return lead
        except BaseException:
            self.session.rollback()
            raise

    # One reply, reloaded from the database; None for an unknown id. Same handling as get_lead.
    def get_reply(self, reply_id: int) -> Reply | None:
        try:
            reply = self.session.get(Reply, reply_id, populate_existing=True)
            self.session.commit()
            return reply
        except BaseException:
            self.session.rollback()
            raise

    # Stores the classifier's result and the router's flags. The classification can be None
    # (the AI failed); the flags from the decision are then still saved so staff see the lead.
    # Refuses an anonymized lead: new text about the patient would never be cleaned again.
    # Only while the lead is still "new" (InvalidTransition otherwise): a pipeline result that
    # arrives after the deadline fallback already acknowledged the lead is discarded, not written.
    # It updates the lead row, so it locks it with the stronger FOR NO KEY UPDATE (see below). The
    # status is read AFTER the lock, so a writer that is moving the status right now is waited for.
    def set_classification(
        self, lead_id: int, *, classification: Classification | None, decision: Decision,
    ) -> Lead:
        try:
            self._refuse_if_anonymized(lead_id, will_update_lead=True)
            lead = self._require(lead_id, fresh=True)
            if lead.status != LeadStatus.NEW.value:
                raise InvalidTransition(f"lead {lead_id}: classification only while the lead is new")
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
    # Only moves listed in LEAD_TRANSITIONS are allowed (InvalidTransition otherwise).
    def set_lead_status(self, lead_id: int, status: LeadStatus) -> Lead:
        if not isinstance(status, LeadStatus):
            raise ValueError(f"status must be a LeadStatus, got {status!r}")
        try:
            lead = self._move_status(Lead, lead_id, status, LEAD_TRANSITIONS, {})
            self.session.commit()
            return lead
        except BaseException:
            self.session.rollback()
            raise

    # The FIRST reply of a lead (an acknowledgment or an auto-reply), written by whoever gets there
    # first: the pipeline, the deadline fallback or the sweep. Exactly one of them wins; every other
    # gets InvalidTransition and writes nothing, so the patient gets one email, not two.
    # The status move and the reply insert must be one step, or a crash leaves a lead that looks
    # handled with no reply: both happen in this one transaction and are rolled back together.
    # Steps, all under one row lock on the lead:
    #   1. lock the lead FOR NO KEY UPDATE and refuse an anonymized one (ValueError). The strong lock
    #      at once, because step 3 updates the lead row: taking FOR SHARE first and upgrading later
    #      deadlocks two writers (the 4a lesson). The lock makes all writers of one lead take turns.
    #   2. refuse a suppressed lead (the per-address limit left it without email on purpose: a
    #      suppressed lead gets no email, whoever asks), then refuse if the lead already has a first
    #      reply (kind acknowledgment or auto_reply). Both read after the lock, so a writer sees what
    #      suppress_lead or the winner of the race committed. The first-reply check is the real
    #      guard: an "acknowledged" lead without a reply is allowed (the sweep's case).
    #   3. ACKNOWLEDGMENT moves new/classified -> acknowledged (an acknowledged lead stays);
    #      AUTO_REPLY never moves the status (the pipeline moves it to auto_replied after the send).
    #      Any other status is past the first reply: InvalidTransition.
    #   4. insert the reply as "drafted" (the sender claims it next) and commit.
    # Lock order is the same as retention's (lead, then replies), so the two cannot deadlock.
    def acknowledge_lead(
        self, lead_id: int, *, kind: Action, ai_body: str | None, body: str, now: datetime,
        needs_manual_review: bool = False,
    ) -> Reply:
        # isinstance first: Action is a StrEnum, so a plain "acknowledgment" string would pass `in`.
        if not isinstance(kind, Action) or kind not in FIRST_REPLY_KINDS:
            raise ValueError(f"kind must be ACKNOWLEDGMENT or AUTO_REPLY, got {kind!r}")
        _require_aware("now", now)
        try:
            self._refuse_if_anonymized(lead_id, will_update_lead=True)
            row = self.session.execute(
                select(Lead.status, Lead.send_suppressed).where(Lead.id == lead_id)
            ).one_or_none()
            if row is None:
                raise LookupError(f"lead {lead_id} not found")
            status, suppressed = row
            if suppressed:
                raise InvalidTransition(f"lead {lead_id} is suppressed: no email on purpose")
            if self._has_first_reply(lead_id):
                raise InvalidTransition(f"lead {lead_id} already has a first reply")
            flags = {"needs_manual_review": True} if needs_manual_review else {}
            if kind is Action.ACKNOWLEDGMENT and status in (LeadStatus.NEW, LeadStatus.CLASSIFIED):
                self._move_status(Lead, lead_id, LeadStatus.ACKNOWLEDGED, LEAD_TRANSITIONS, flags)
            elif status in AWAITING_FIRST_REPLY:
                if flags:
                    self.session.execute(update(Lead).where(Lead.id == lead_id).values(**flags))
            else:
                raise InvalidTransition(f"lead {lead_id}: no first reply from status {status}")
            reply = Reply(
                lead_id=lead_id, kind=kind.value,
                ai_body=_clean(ai_body) if ai_body is not None else None,
                body=_clean(body), status=ReplyStatus.DRAFTED.value, created_at=now,
            )
            self.session.add(reply)
            self.session.commit()
            return reply
        except BaseException:
            self.session.rollback()
            raise

    # "No email on purpose": the per-address limit tripped. The lead moves new -> acknowledged with
    # no reply, flagged for staff, and send_suppressed tells the sweep to leave it alone (otherwise
    # the sweep would send the very email the limit stopped). Only from "new" (InvalidTransition
    # otherwise): the limit is checked right after the lead is saved, before anything else ran.
    # Refuses an anonymized lead (ValueError) under FOR NO KEY UPDATE, because it updates the lead row.
    def suppress_lead(self, lead_id: int) -> Lead:
        try:
            self._refuse_if_anonymized(lead_id, will_update_lead=True)
            lead = self._move_status(
                Lead, lead_id, LeadStatus.ACKNOWLEDGED, SUPPRESS_TRANSITIONS,
                {"send_suppressed": True, "needs_manual_review": True},
            )
            self.session.commit()
            return lead
        except BaseException:
            self.session.rollback()
            raise

    # Puts a lead on the staff review list without touching its status (e.g. the deadline expired
    # or a send gave up). One UPDATE, which locks the row itself; no read first, so nothing to race.
    # Allowed on an anonymized lead: a flag is not personal data.
    def flag_for_review(self, lead_id: int) -> Lead:
        try:
            lead = self.session.scalars(
                update(Lead).where(Lead.id == lead_id).values(needs_manual_review=True).returning(Lead),
                execution_options={"populate_existing": True},
            ).one_or_none()
            if lead is None:
                raise LookupError(f"lead {lead_id} not found")
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

    # Moves a reply to a new status, only along REPLY_TRANSITIONS (InvalidTransition otherwise:
    # "sent" and "rejected" are final, and a second session that still believes the reply is
    # "approved" cannot send it again). sent_at is set only when the new status is "sent".
    # Moving to "sending" IS the claim: the same UPDATE stamps claimed_at and adds 1 to
    # send_attempts, so winning the claim and counting the attempt cannot come apart. The one
    # session that wins is the only one allowed to send; every other gets InvalidTransition.
    # Callers commit the claim (this method does) BEFORE the email goes out.
    # `error` is assigned every time (None clears it: a successful resend must not keep an old
    # error); approved_by and edited are only changed when given, so later steps do not wipe them.
    # An `error` text may hold the recipient's address (personal data): for an anonymized lead the
    # status still moves but the error is dropped. The lead is checked under a lock first, then the
    # reply is updated: the same order as retention (lead, then replies), so they cannot deadlock.
    def set_reply_status(
        self, reply_id: int, status: ReplyStatus, *, now: datetime, error: str | None = None,
        approved_by: int | None = None, edited: bool | None = None,
    ) -> Reply:
        if not isinstance(status, ReplyStatus):
            raise ValueError(f"status must be a ReplyStatus, got {status!r}")
        _require_aware("now", now)
        try:
            if error is not None:
                lead_id = self.session.scalar(select(Reply.lead_id).where(Reply.id == reply_id))
                if lead_id is not None and self._anonymized_under_lock(lead_id):
                    error = None
            values: dict = {"error": _clean(error) if error is not None else None}
            if status is ReplyStatus.SENT:
                values["sent_at"] = now
            if status is ReplyStatus.SENDING:
                values["claimed_at"] = now
                values["send_attempts"] = Reply.send_attempts + 1  # computed by the database, not read first
            if approved_by is not None:
                values["approved_by"] = approved_by
            if edited is not None:
                values["edited"] = edited
            reply = self._move_status(Reply, reply_id, status, REPLY_TRANSITIONS, values)
            self.session.commit()
            return reply
        except BaseException:
            self.session.rollback()
            raise

    # Sweep part 1: gives up a claim nobody finished, sending -> failed, so the sweep can claim the
    # reply again (failed -> sending) and resend it. ONE conditional UPDATE that re-checks the
    # stuck condition: status is still "sending" AND claimed_at is still before `claimed_before`.
    # Why both: the sweep read its list earlier; if another sweep has resent the reply since, the
    # status is "sending" again but with a fresh claimed_at, and a status-only check would let this
    # sweep take over a send in flight and email a second copy. 0 rows: InvalidTransition.
    # `error` must hold no personal data (the sweep passes a fixed text), so no lead lock is needed:
    # this touches only the reply row. sending -> failed is in REPLY_TRANSITIONS.
    def fail_stuck_reply(self, reply_id: int, *, claimed_before: datetime, error: str) -> Reply:
        _require_aware("claimed_before", claimed_before)
        try:
            reply = self.session.scalars(
                update(Reply)
                .where(
                    Reply.id == reply_id, Reply.status == ReplyStatus.SENDING.value,
                    Reply.claimed_at < claimed_before,
                )
                .values(status=ReplyStatus.FAILED.value, error=_clean(error))
                .returning(Reply),
                execution_options={"populate_existing": True},
            ).one_or_none()
            if reply is None:
                raise InvalidTransition(f"replies {reply_id}: not a stuck sending reply (any more)")
            self.session.commit()
            return reply
        except BaseException:
            self.session.rollback()
            raise

    # All replies of one lead, oldest first (id breaks ties between equal timestamps).
    # Same transaction handling and fresh reload as get_lead.
    def replies_for(self, lead_id: int) -> list[Reply]:
        try:
            replies = list(self.session.scalars(
                select(Reply).where(Reply.lead_id == lead_id).order_by(Reply.created_at, Reply.id),
                execution_options={"populate_existing": True},
            ))
            self.session.commit()
            return replies
        except BaseException:
            self.session.rollback()
            raise

    # How many leads for one mailbox arrived at or after `since` (created_at >= since). The
    # per-address limit calls it right after saving the new lead, so the count includes that lead.
    # Served by ix_leads_mailbox_key_created_at (equal key, then a time range).
    def count_leads_for_mailbox_since(self, mailbox_key: str, since: datetime) -> int:
        _require_aware("since", since)
        return self._count(Lead.mailbox_key == mailbox_key, Lead.created_at >= since)

    # How many leads arrived at or after `since`, for the global cap on AI runs per hour.
    # Served by ix_leads_created_at.
    def count_leads_since(self, since: datetime) -> int:
        _require_aware("since", since)
        return self._count(Lead.created_at >= since)

    # Sweep part 1: replies of one kind stuck in "sending" (outcome unknown) whose claim is older
    # than `claimed_before` (strictly) and that have been tried fewer than `max_attempts` times.
    # The sweep asks only for acknowledgments; a draft is never resent automatically.
    # A plain read, no lock: the sweep then moves each one with fail_stuck_reply, a conditional
    # write that re-checks the stuck condition, which is what stops two sweeps from both acting on it.
    def stuck_sending_replies(
        self, *, kind: Action, claimed_before: datetime, max_attempts: int,
    ) -> list[Reply]:
        if not isinstance(kind, Action):
            raise ValueError(f"kind must be an Action, got {kind!r}")
        _require_aware("claimed_before", claimed_before)
        return self._read_all(
            select(Reply)
            .where(
                Reply.kind == kind.value, Reply.status == ReplyStatus.SENDING.value,
                Reply.claimed_at < claimed_before, Reply.send_attempts < max_attempts,
            )
            .order_by(Reply.id)
        )

    # Sweep part 2: leads created before `created_before` (strictly) that are still waiting for a
    # first reply (new, classified or acknowledged), not suppressed on purpose, not anonymized, and
    # with no acknowledgment or auto-reply. A plain read, no lock: the sweep acts on each through
    # acknowledge_lead, whose lock and checks decide who wins.
    def leads_without_first_reply(self, *, created_before: datetime) -> list[Lead]:
        _require_aware("created_before", created_before)
        return self._read_all(
            select(Lead)
            .where(
                Lead.status.in_([s.value for s in AWAITING_FIRST_REPLY]),
                Lead.send_suppressed.is_(False),
                Lead.anonymized_at.is_(None),
                Lead.created_at < created_before,
                ~self._first_reply_exists(Lead.id),
            )
            .order_by(Lead.id)
        )

    # Sweep part 1b: first replies (acknowledgment, auto-reply) still "drafted", created before
    # `created_before` (strictly), of a lead that is not anonymized. They were committed by
    # acknowledge_lead but never claimed (the writer crashed or the database failed before the
    # send), and part 2 skips their leads because a first reply exists. Never kind "draft": a staff
    # draft waits for approval. A plain read, no lock: the sweep sends each through send(), whose
    # claim lets one sweep win.
    def stale_drafted_first_replies(self, *, created_before: datetime) -> list[Reply]:
        _require_aware("created_before", created_before)
        return self._read_all(
            select(Reply)
            .join(Lead, Lead.id == Reply.lead_id)
            .where(
                Reply.kind.in_([k.value for k in FIRST_REPLY_KINDS]),
                Reply.status == ReplyStatus.DRAFTED.value,
                Reply.created_at < created_before,
                Lead.anonymized_at.is_(None),
            )
            .order_by(Reply.id)
        )

    # DSGVO retention: blank the personal data of every lead created BEFORE `cutoff` (strictly:
    # a lead exactly at the cutoff stays) that is not anonymized yet. Reply texts, reply errors
    # (they may hold a recipient address), the dedup_key hash, the mailbox_key (the normalized
    # address) and ai_reason (free text about the
    # patient) count as personal data too. Category, risk, urgency, language, statuses and
    # timestamps stay, so the statistics survive. Audit rows are not touched (they only point
    # at ids).
    # Work is done in batches of `batch_size` leads, each batch ONE short transaction (select the
    # next ids FOR UPDATE, blank their replies and the leads, commit), until none are left. This
    # avoids a giant IN list and long locks. A crash mid-run is safe: finished batches stay
    # done, the `anonymized_at IS NULL` filter makes a re-run continue; a later run returns 0.
    # There is no clean-up pass: personal data cannot re-enter an anonymized lead, because every
    # write path checks the lead under a lock that waits for this method's FOR UPDATE on the batch.
    # Returns how many leads were newly anonymized.
    def anonymize_older_than(
        self, *, cutoff: datetime, now: datetime, batch_size: int = RETENTION_BATCH_SIZE,
    ) -> int:
        _require_aware("cutoff", cutoff)
        _require_aware("now", now)
        if not 1 <= batch_size <= MAX_RETENTION_BATCH_SIZE:
            raise ValueError(f"batch_size must be between 1 and {MAX_RETENTION_BATCH_SIZE}")
        total = 0
        try:
            while True:
                # FOR UPDATE: two runs at once take turns per batch and share the work, each lead once.
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
                        dedup_key=None, mailbox_key=None, ai_reason=None, anonymized_at=now,
                    )
                )
                self.session.commit()
                total += len(ids)
            self.session.commit()  # ends the transaction the last (empty) batch query opened
            # Loaded Lead/Reply objects in this session are now stale; drop them so callers re-read.
            self.session.expire_all()
            return total
        except BaseException:
            self.session.rollback()
            raise

    # Raises ValueError for an anonymized lead.
    def _refuse_if_anonymized(self, lead_id: int, *, will_update_lead: bool = False) -> None:
        if self._anonymized_under_lock(lead_id, will_update_lead=will_update_lead):
            raise ValueError(f"lead {lead_id} is anonymized")

    # True if the lead is anonymized, read from the database (not the cache) under a row lock.
    # Why a lock: a plain read does not wait for a running retention batch, still sees "not
    # anonymized", and the write after it would put patient text on a blanked lead. The lock waits
    # for the batch, then reads its result, and lasts until the caller's commit/rollback.
    # Which lock: callers that only INSERT/UPDATE other rows (add_reply, set_reply_status) take
    # FOR SHARE. A caller that then UPDATEs the lead row itself (set_classification) must take
    # FOR NO KEY UPDATE: two sessions holding FOR SHARE would each wait for the other to let go
    # before updating (a deadlock). FOR NO KEY UPDATE makes them take turns instead.
    def _anonymized_under_lock(self, lead_id: int, *, will_update_lead: bool = False) -> bool:
        stmt = select(Lead.anonymized_at).where(Lead.id == lead_id)
        stmt = stmt.with_for_update(key_share=True) if will_update_lead else stmt.with_for_update(read=True)
        return self.session.scalar(stmt) is not None

    # The status change as ONE conditional statement (compare-and-set):
    #   UPDATE <table> SET status = :new, ... WHERE id = :id AND status IN (the statuses that may move to :new)
    # The database checks the CURRENT status and changes it in the same step, under the row lock, so
    # two sessions racing for the same move cannot both succeed: the second waits, re-checks the
    # WHERE and finds 0 rows. (A read-then-write in Python would let both through.)
    # RETURNING hands back the fresh row; populate_existing refreshes an object this session
    # already holds, so a cached copy never hides the change. Does not commit: callers do.
    # Step 1 only asks "does this id exist?" (LookupError), it decides nothing about the move.
    # Step 2 is the real check. 0 rows there means the current status does not allow the move
    # (or another session changed it first): InvalidTransition. Rows are never deleted, so a
    # row that passed step 1 cannot disappear before step 2.
    def _move_status(self, model, row_id: int, new, table: dict, values: dict):
        if self.session.scalar(select(model.id).where(model.id == row_id)) is None:
            raise LookupError(f"{model.__tablename__} {row_id} not found")
        sources = [old.value for old, targets in table.items() if new in targets]
        stmt = (
            update(model)
            .where(model.id == row_id, model.status.in_(sources))
            .values(status=new.value, **values)
            .returning(model)
        )
        row = self.session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()
        if row is None:
            raise InvalidTransition(
                f"{model.__tablename__} {row_id}: moving to {new.value} is not allowed from its current status"
            )
        return row

    # EXISTS (a reply of kind acknowledgment or auto_reply for this lead). `lead_id` is a value or a
    # column (Lead.id), so the same condition serves acknowledge_lead and the sweep query.
    def _first_reply_exists(self, lead_id):
        return (
            select(Reply.id)
            .where(Reply.lead_id == lead_id, Reply.kind.in_([k.value for k in FIRST_REPLY_KINDS]))
            .exists()
        )

    def _has_first_reply(self, lead_id: int) -> bool:
        return bool(self.session.scalar(select(self._first_reply_exists(lead_id))))

    # select(count) WHERE <conditions>, ending its transaction like the other reads.
    def _count(self, *conditions) -> int:
        try:
            n = self.session.scalar(select(func.count()).select_from(Lead).where(*conditions))
            self.session.commit()
            return n
        except BaseException:
            self.session.rollback()
            raise

    # Runs a SELECT of ORM rows, reloads them fresh, ends the transaction (same as replies_for).
    def _read_all(self, stmt) -> list:
        try:
            rows = list(self.session.scalars(stmt, execution_options={"populate_existing": True}))
            self.session.commit()
            return rows
        except BaseException:
            self.session.rollback()
            raise

    # fresh=True reloads from the database instead of trusting this session's cached copy (needed
    # when the caller decides something from the row, e.g. its status, after taking the lock).
    def _require(self, lead_id: int, *, fresh: bool = False) -> Lead:
        lead = self.session.get(Lead, lead_id, populate_existing=fresh)
        if lead is None:
            raise LookupError(f"lead {lead_id} not found")
        return lead
