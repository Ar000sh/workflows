"""The four tables from spec section 5 as typed SQLAlchemy models.

Status-like columns are plain strings guarded by CHECK constraints, so the
DATABASE rejects unknown values, not only our Python code. All timestamps are
timestamptz. The migrations in alembic/versions must always match this file
(a test compares them).
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class LeadStatus(StrEnum):
    NEW = "new"
    CLASSIFIED = "classified"
    AUTO_REPLIED = "auto_replied"
    ACKNOWLEDGED = "acknowledged"
    AWAITING_APPROVAL = "awaiting_approval"
    REPLIED = "replied"
    DONE = "done"


class ReplyStatus(StrEnum):
    DRAFTED = "drafted"
    APPROVED = "approved"
    SENT = "sent"
    REJECTED = "rejected"
    FAILED = "failed"


# Which status may follow which: "key -> the statuses it may move to". The repository turns this into
# ONE conditional UPDATE (... WHERE status IN the allowed sources), so a stale or racing session
# cannot make a move that is no longer legal (two sends, or sent -> failed). An empty set = final.
# Lead flow is the spec's section 5; a classifier failure or an unsupported language skips
# "classified" -> "auto_replied" and goes straight to "acknowledged" (routes 1 and 2).
LEAD_TRANSITIONS: dict[LeadStatus, frozenset[LeadStatus]] = {
    LeadStatus.NEW: frozenset({LeadStatus.CLASSIFIED, LeadStatus.ACKNOWLEDGED}),
    LeadStatus.CLASSIFIED: frozenset({LeadStatus.AUTO_REPLIED, LeadStatus.ACKNOWLEDGED}),
    LeadStatus.AUTO_REPLIED: frozenset({LeadStatus.DONE}),
    # DONE straight from acknowledged: routes 1 and 2 have no draft, staff handle them by phone.
    LeadStatus.ACKNOWLEDGED: frozenset({LeadStatus.AWAITING_APPROVAL, LeadStatus.DONE}),
    LeadStatus.AWAITING_APPROVAL: frozenset({LeadStatus.REPLIED, LeadStatus.DONE}),
    LeadStatus.REPLIED: frozenset({LeadStatus.DONE}),
    LeadStatus.DONE: frozenset(),
}

# Replies: a draft is approved or rejected by staff; an approved (or system-made) reply is sent or
# fails; a failed one may be resent or fail again (the new error replaces the old). "drafted" may go
# straight to sent/failed because acknowledgments and auto-replies need no approval.
# SENT and REJECTED are final: "sent is final" (spec section 7) is this table.
REPLY_TRANSITIONS: dict[ReplyStatus, frozenset[ReplyStatus]] = {
    ReplyStatus.DRAFTED: frozenset(
        {ReplyStatus.APPROVED, ReplyStatus.REJECTED, ReplyStatus.SENT, ReplyStatus.FAILED}
    ),
    ReplyStatus.APPROVED: frozenset({ReplyStatus.SENT, ReplyStatus.FAILED}),
    ReplyStatus.FAILED: frozenset({ReplyStatus.SENT, ReplyStatus.FAILED}),
    ReplyStatus.SENT: frozenset(),
    ReplyStatus.REJECTED: frozenset(),
}

# Same strings as router.Action, written out so the DB layer does not import the router.
REPLY_KINDS = ("auto_reply", "acknowledgment", "draft")
AUDIT_ACTIONS = ("approve", "edit", "reject", "resend", "login", "logout")


# Builds "col IN ('a','b')" from our own constant values (never from user input).
def _in_list(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(str(v)) for v in values)})"


class Base(DeclarativeBase):
    pass


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint(_in_list("status", LeadStatus), name="ck_leads_status"),
        # Length limits as CHECKs (not VARCHAR(n)): an overlong value then raises IntegrityError,
        # the same error family as every other rule, instead of a separate DataError.
        CheckConstraint("char_length(name) <= 200", name="ck_leads_name_len"),
        CheckConstraint("char_length(email) <= 254", name="ck_leads_email_len"),
        CheckConstraint("char_length(phone) <= 50", name="ck_leads_phone_len"),
        CheckConstraint("char_length(message) <= 10000", name="ck_leads_message_len"),
        # An index is a sorted lookup list: without one, Postgres reads every row to answer a query.
        # Each one below costs a little on every insert, so only the queries we really run get one.
        # Dashboard lists newest first, and retention asks "older than 90 days" (range on time).
        Index("ix_leads_created_at", "created_at"),
        # Dashboard queues ("awaiting_approval", "new") filter by status. Not used by code until 4d.
        Index("ix_leads_status", "status"),
        # Duplicate detection looks up "same dedup_key in the last 2 minutes": equal key, then a
        # time range. Both columns in one index, so that lookup stays fast on every submission.
        Index("ix_leads_dedup_key_created_at", "dedup_key", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    channel: Mapped[str] = mapped_column(String(30), default="web_form")
    # Personal data. Length limits live in the DB too; nullable because retention blanks them.
    name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    # DSGVO: no consent, no row.
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    category: Mapped[str | None] = mapped_column(String(30))
    risk: Mapped[str | None] = mapped_column(String(20))
    urgency: Mapped[str | None] = mapped_column(String(20))
    language: Mapped[str | None] = mapped_column(String(10))
    language_unsupported: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    ai_reason: Mapped[str | None] = mapped_column(Text)
    needs_manual_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    status: Mapped[str] = mapped_column(String(30), default=LeadStatus.NEW.value)
    # "Anonymized" = nobody can tell who this patient was any more. After 90 days the retention
    # job blanks name, email, phone, message and the reply texts (DSGVO: keep personal data only
    # as long as needed) but keeps category, risk, status and dates, so statistics still work.
    # NULL = still holds personal data; a timestamp = blanked at that moment. Retention skips
    # leads that have it, and the repository refuses to attach new personal data to them.
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Duplicate fingerprint: sha256 hex of lower-cased email + NUL + stripped message. Two
    # submissions with the same key within 2 minutes are one lead (a double click, a retry).
    # A hash, so we compare without storing a second copy of the text, but it is still derived
    # from personal data, so retention blanks it.
    dedup_key: Mapped[str | None] = mapped_column(Text)


class StaffUser(Base):
    __tablename__ = "staff_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)  # argon2
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Reply(Base):
    __tablename__ = "replies"
    __table_args__ = (
        CheckConstraint(_in_list("kind", REPLY_KINDS), name="ck_replies_kind"),
        CheckConstraint(_in_list("status", ReplyStatus), name="ck_replies_status"),
        # "All replies of this lead" (replies_for) and, in 4d, "which leads have a drafted reply".
        # lead_id comes first, so it also serves plain lead_id lookups (Postgres does not index
        # foreign keys by itself).
        Index("ix_replies_lead_id_status", "lead_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"))
    kind: Mapped[str] = mapped_column(String(30))
    ai_body: Mapped[str | None] = mapped_column(Text)  # what the AI wrote
    body: Mapped[str | None] = mapped_column(Text)     # what is actually sent (after staff edits)
    status: Mapped[str] = mapped_column(String(30), default=ReplyStatus.DRAFTED.value)
    error: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("staff_users.id"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # NULL = not yet decided; True/False once a human approved the draft (edited or unchanged).
    edited: Mapped[bool | None] = mapped_column(Boolean)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (CheckConstraint(_in_list("action", AUDIT_ACTIONS), name="ck_audit_log_action"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Nullable: a failed login for an unknown address has no user, login/logout have no lead.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("staff_users.id"))
    action: Mapped[str] = mapped_column(String(30))
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"))
    reply_id: Mapped[int | None] = mapped_column(ForeignKey("replies.id"))
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
