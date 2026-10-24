"""ReplyService: sends one reply by email, exactly once, and records what happened.

The flow of send(reply_id) (spec section 6 "Sending"):
  1. read the reply and its lead (fresh from the database); no address left -> refuse, no claim
  2. check the kind may be sent from its status (a draft needs staff approval first)
  3. CLAIM: reply -> "sending" in one conditional UPDATE that commits. Only the winner continues;
     a second worker or a double click gets "already handled" and sends nothing
  4. read the address again (retention may have blanked it meanwhile), then call the sender with
     no transaction open and no connection held: a slow SMTP server must not block other writers
  5. record "sent" or "failed"; a timeout records nothing, so the reply stays "sending" (unknown)
  6. for a first reply, fix the lead: an auto-reply that went out makes the lead "auto_replied";
     one that failed or is in doubt puts the lead in the staff queue instead
This is the claim-before-send (outbox) pattern: the database says who may send BEFORE the email
leaves, because nothing can take an email back afterwards.
"""
from __future__ import annotations

import logging
from datetime import datetime
from enum import StrEnum
from typing import Callable

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lead_responder import templates
from lead_responder.db.models import Lead, LeadStatus, ReplyStatus
from lead_responder.practice import Practice
from lead_responder.prompting import sanitize_name
from lead_responder.repository import InvalidTransition, Repository
from lead_responder.router import COMBINED_LANGUAGE, Action
from lead_responder.sender import Outcome, Sender, SendResult

log = logging.getLogger(__name__)


class SendOutcome(StrEnum):
    SENT = "sent"                        # the provider accepted it, recorded "sent"
    FAILED = "failed"                    # known not sent, recorded "failed" (staff can resend)
    IN_DOUBT = "in_doubt"                # may have gone out; the reply stays "sending"
    ALREADY_HANDLED = "already_handled"  # another sender won the claim, or it is sent/rejected/in flight
    REFUSED = "refused"                  # must not be sent: no address any more, or a draft not approved


# The statuses each kind may be claimed from. A business rule, so it lives here: the transition
# table allows drafted -> sending for every kind (acknowledgments and auto-replies need it), so
# without this check an unapproved staff draft could be emailed.
SEND_FROM: dict[Action, frozenset[ReplyStatus]] = {
    Action.ACKNOWLEDGMENT: frozenset({ReplyStatus.DRAFTED, ReplyStatus.FAILED}),
    Action.AUTO_REPLY: frozenset({ReplyStatus.DRAFTED, ReplyStatus.FAILED}),
    Action.DRAFT: frozenset({ReplyStatus.APPROVED, ReplyStatus.FAILED}),
}
# A refused reply in one of these moves to "failed" (the table allows it); a failed one stays.
_REFUSE_TO_FAILED = (ReplyStatus.DRAFTED, ReplyStatus.APPROVED, ReplyStatus.SENDING)


# True when the lead has no address to write to: retention blanked it (DSGVO) or it is empty.
def _no_address(lead: Lead) -> bool:
    return lead.anonymized_at is not None or not (lead.email or "").strip()


class ReplyService:
    # session_factory makes one short-lived Session per call (sessionmaker from db/session.py);
    # clock gives "now" (timezone-aware) so tests control the timestamps.
    def __init__(
        self, *, session_factory: Callable[[], Session], sender: Sender, practice: Practice,
        clock: Callable[[], datetime],
    ):
        self._session_factory = session_factory
        self._sender = sender
        self._practice = practice
        self._clock = clock

    # Sends one reply (see the module docstring for the steps). Safe to call twice or from two
    # workers at once: the claim lets exactly one of them send.
    def send(self, reply_id: int) -> SendOutcome:
        return self._send(reply_id, subject_language=None)

    # Route-1 fallback (deadline expired, pipeline crashed, sweep found a lead with no reply): the
    # fixed DE+EN acknowledgment with the emergency numbers, chosen by whether the practice is open
    # now; the name goes through sanitize_name because nobody reviews this text. acknowledge_lead
    # writes it and flags the lead in one transaction; if another writer already gave the lead its
    # first reply we get InvalidTransition and return False without sending. True means this call
    # wrote the acknowledgment and attempted the send (the send's own outcome is recorded on the reply).
    def acknowledge_with_fallback(self, lead_id: int) -> bool:
        now = self._clock()
        with self._session_factory() as session:
            repo = Repository(session)
            lead = repo.get_lead(lead_id)
            if lead is None:
                raise LookupError(f"lead {lead_id} not found")
            body = templates.acknowledgment(
                COMBINED_LANGUAGE, name=sanitize_name(lead.name), practice=self._practice,
                emergency=True, practice_open=self._practice.is_open(now),
            )
            try:
                reply = repo.acknowledge_lead(
                    lead_id, kind=Action.ACKNOWLEDGMENT, ai_body=None, body=body, now=now,
                    needs_manual_review=True,
                )
            except InvalidTransition:
                log.info("lead %s: fallback acknowledgment skipped, first reply exists", lead_id)
                return False
            reply_id = reply.id
        self._send(reply_id, subject_language=COMBINED_LANGUAGE)
        return True

    # The steps of send(). subject_language overrides the lead's language for the subject (the
    # fallback acknowledgment is bilingual whatever the lead's language is).
    def _send(self, reply_id: int, *, subject_language: str | None) -> SendOutcome:
        with self._session_factory() as session:
            repo = Repository(session)
            # Step 1: fresh reads; each Repository call commits, so no snapshot is kept.
            reply = repo.get_reply(reply_id)
            if reply is None:
                raise LookupError(f"reply {reply_id} not found")
            kind, status, lead_id = Action(reply.kind), ReplyStatus(reply.status), reply.lead_id
            lead = repo.get_lead(lead_id)
            if _no_address(lead):
                return self._refuse(repo, reply_id, status)
            # Step 2: the service's rule on top of the transition table.
            if status not in SEND_FROM[kind]:
                if kind is Action.DRAFT and status is ReplyStatus.DRAFTED:
                    log.warning("reply %s: refused, draft not approved", reply_id)
                    return SendOutcome.REFUSED
                return SendOutcome.ALREADY_HANDLED
            # Step 3: the claim. It commits before anything is sent; the loser stops here.
            try:
                claimed = repo.set_reply_status(reply_id, ReplyStatus.SENDING, now=self._clock())
            except InvalidTransition:
                log.info("reply %s: claim lost, already handled", reply_id)
                return SendOutcome.ALREADY_HANDLED
            # Step 4: the address again, after the claim (spec 11f), and the body from the claimed row.
            lead = repo.get_lead(lead_id)
            if _no_address(lead):
                return self._refuse(repo, reply_id, ReplyStatus.SENDING)
            to, body = lead.email, claimed.body
            subject = templates.subject(subject_language or lead.language, self._practice)
            # close() ends any transaction and gives the connection back to the pool for the
            # duration of the send; the Session is reusable afterwards for the recording.
            session.close()
            result = self._sender.send(to=to, subject=subject, body=body)
            # Step 5 and 6.
            outcome = self._record(repo, reply_id, result)
            self._update_lead(repo, lead_id, kind, outcome)
            return outcome

    # No address: nothing is sent and nothing is claimed. The reply moves to "failed" with no error
    # text (there is no address to mention), so it leaves the "to send" lists; already "failed"
    # stays as it is. InvalidTransition = someone else moved it meanwhile, which is fine.
    def _refuse(self, repo: Repository, reply_id: int, status: ReplyStatus) -> SendOutcome:
        if status in _REFUSE_TO_FAILED:
            try:
                repo.set_reply_status(reply_id, ReplyStatus.FAILED, now=self._clock(), error=None)
            except InvalidTransition:
                pass
        log.warning("reply %s: refused, lead has no address", reply_id)
        return SendOutcome.REFUSED

    # Writes the sender's result. The stored error is the exception class name only (SendResult
    # guarantees it). If the database fails while recording (probe P8), or a staff member already
    # moved the reply, it stays as it is ("sending" = unknown) and the answer is IN_DOUBT: an
    # accepted email recorded as "failed" would invite a second copy.
    def _record(self, repo: Repository, reply_id: int, result: SendResult) -> SendOutcome:
        now = self._clock()
        try:
            if result.outcome is Outcome.ACCEPTED:
                repo.set_reply_status(reply_id, ReplyStatus.SENT, now=now)
                log.info("reply %s: sent", reply_id)
                return SendOutcome.SENT
            if result.outcome is Outcome.FAILED:
                repo.set_reply_status(reply_id, ReplyStatus.FAILED, now=now, error=result.detail or None)
                log.warning("reply %s: send failed (%s)", reply_id, result.detail)
                return SendOutcome.FAILED
        except (SQLAlchemyError, InvalidTransition) as e:
            log.error("reply %s: recording the %s result failed (%s)", reply_id, result.outcome, type(e).__name__)
            return SendOutcome.IN_DOUBT
        log.warning("reply %s: send timed out, outcome unknown", reply_id)
        return SendOutcome.IN_DOUBT

    # The lead's status after a first reply (spec section 4 "Lead status vs send outcome"): only a
    # sent auto-reply makes the lead "auto_replied". A failed or in-doubt auto-reply flags the lead
    # and moves it classified -> acknowledged (the staff queue); a failed or in-doubt
    # acknowledgment only flags it (the lead is already "acknowledged"). Drafts change nothing here:
    # staff sent them and see the result. InvalidTransition = another writer already moved the lead.
    def _update_lead(self, repo: Repository, lead_id: int, kind: Action, outcome: SendOutcome) -> None:
        if kind is Action.DRAFT or outcome not in (SendOutcome.SENT, SendOutcome.FAILED, SendOutcome.IN_DOUBT):
            return
        if outcome is SendOutcome.SENT:
            if kind is Action.AUTO_REPLY:
                self._move_lead(repo, lead_id, LeadStatus.AUTO_REPLIED)
            return
        repo.flag_for_review(lead_id)
        if kind is Action.AUTO_REPLY:
            self._move_lead(repo, lead_id, LeadStatus.ACKNOWLEDGED)

    @staticmethod
    def _move_lead(repo: Repository, lead_id: int, status: LeadStatus) -> None:
        try:
            repo.set_lead_status(lead_id, status)
        except InvalidTransition:
            log.info("lead %s: not moved to %s, already moved by another writer", lead_id, status)
