"""ReplyService: sends one reply by email, exactly once, and records what happened.

The flow of send(reply_id) (spec section 6 "Sending"):
  1. read the reply (fresh from the database)
  2. check the kind may be sent from its status (a draft needs staff approval first; a reply in
     "sending" belongs to whoever claimed it), then read the lead: no address -> refuse, no claim
  3. CLAIM: reply -> "sending" in one conditional UPDATE that commits. Only the winner continues;
     a second worker or a double click gets "already handled" and sends nothing
  4. read the address again (retention may have blanked it meanwhile), then call the sender with
     no transaction open and no connection held: a slow SMTP server must not block other writers.
     A sender that raises instead of returning a result counts as a timeout (in doubt)
  5. record "sent" or "failed"; a timeout records nothing, so the reply stays "sending" (unknown)
  6. for a first reply, fix the lead: an auto-reply that went out makes the lead "auto_replied";
     one that failed or is in doubt puts the lead in the staff queue instead
This is the claim-before-send (outbox) pattern: the database says who may send BEFORE the email
leaves, because nothing can take an email back afterwards.

sweep() is the scheduled clean-up (spec section 6 "Sweep", run by scripts/run_sweep.py):
  1. acknowledgments stuck in "sending" (claimed over SENDING_TIMEOUT_MINUTES ago): sending ->
     failed, then send() again, up to MAX_SEND_ATTEMPTS claims; at the cap they wait for staff.
     Drafts and auto-replies in "sending" are never resent: a duplicate medical reply is not harmless
  1b. acknowledgments and auto-replies still "drafted" (written, never claimed: the writer died
     or the database failed before the send) and older than NO_REPLY_SWEEP_MINUTES: send() them.
     Never a staff draft (kind "draft"): it waits for approval
  2. leads older than NO_REPLY_SWEEP_MINUTES with no first reply: the fallback acknowledgment
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Callable

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lead_responder import config, templates
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
# "sending" is passed only by the claim's owner after its own claim: step 1 sends a reply in
# "sending" away as ALREADY_HANDLED before the address check, so it never moves another's claim.
_REFUSE_TO_FAILED = (ReplyStatus.DRAFTED, ReplyStatus.APPROVED, ReplyStatus.SENDING)

# The error the sweep stores when it gives up a stuck claim. Fixed text: no address, no message.
STUCK_ERROR = "stuck: retried by sweep"
# The sweep reads ALL stuck acknowledgments (this cap is the largest Postgres integer) and splits
# them itself into "resend" and "at the cap, left for staff", so both come from one read.
_NO_ATTEMPT_CAP = 2**31 - 1


# What one sweep did, for the script's printout and the tests.
@dataclass(frozen=True)
class SweepResult:
    retried_acknowledgments: int   # stuck acknowledgments this sweep resent (whatever the new outcome)
    fallback_acknowledgments: int  # leads that got the fallback acknowledgment from this sweep
    left_for_staff: int            # stuck acknowledgments at MAX_SEND_ATTEMPTS, not resent
    sent_stale_drafted: int = 0    # first replies left "drafted" (never claimed) that this sweep sent


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

    # The scheduled clean-up (module docstring). Safe to run twice in a row (the second run finds
    # nothing) and twice at once: every action goes through a conditional write that one sweep
    # wins, fail_stuck_reply and the claim for part 1, the claim for part 1b, acknowledge_lead's
    # lock for part 2. Part 1b runs before part 2 and uses the same age, so a lead whose first
    # reply was written but never sent is picked up here (part 2 skips it: a first reply exists). A lead
    # anonymized between the read and the action is skipped (send refuses it, acknowledge_lead
    # raises ValueError). The reads hold no lock and no transaction while emails go out.
    def sweep(self) -> SweepResult:
        now = self._clock()
        stuck_before = now - timedelta(minutes=config.SENDING_TIMEOUT_MINUTES)
        with self._session_factory() as session:
            repo = Repository(session)
            stuck = repo.stuck_sending_replies(
                kind=Action.ACKNOWLEDGMENT, claimed_before=stuck_before, max_attempts=_NO_ATTEMPT_CAP,
            )
            retry = [r.id for r in stuck if r.send_attempts < config.MAX_SEND_ATTEMPTS]
            left_for_staff = len(stuck) - len(retry)
            retried = sum(self._retry_stuck(repo, reply_id, stuck_before) for reply_id in retry)
            no_reply_before = now - timedelta(minutes=config.NO_REPLY_SWEEP_MINUTES)
            stale_ids = [r.id for r in repo.stale_drafted_first_replies(created_before=no_reply_before)]
            leads = repo.leads_without_first_reply(created_before=no_reply_before)
            lead_ids = [lead.id for lead in leads]
        sent_stale = sum(self._send_stale(reply_id) for reply_id in stale_ids)
        fallbacks = sum(self._fallback(lead_id) for lead_id in lead_ids)
        log.info(
            "sweep: %s retried, %s stale drafted sent, %s fallback, %s left for staff",
            retried, sent_stale, fallbacks, left_for_staff,
        )
        return SweepResult(retried, fallbacks, left_for_staff, sent_stale)

    # Part 1b for one reply: send() claims it, so of two sweeps only one sends (the other gets
    # ALREADY_HANDLED). True if an email was attempted.
    def _send_stale(self, reply_id: int) -> bool:
        outcome = self.send(reply_id)
        return outcome in (SendOutcome.SENT, SendOutcome.FAILED, SendOutcome.IN_DOUBT)

    # Part 1 for one reply: give up the stale claim (InvalidTransition = another sweep or a late
    # result got there first: skip), then send() claims it again. True if an email was attempted.
    def _retry_stuck(self, repo: Repository, reply_id: int, stuck_before: datetime) -> bool:
        try:
            repo.fail_stuck_reply(reply_id, claimed_before=stuck_before, error=STUCK_ERROR)
        except InvalidTransition:
            log.info("reply %s: stuck claim already handled", reply_id)
            return False
        outcome = self.send(reply_id)
        return outcome in (SendOutcome.SENT, SendOutcome.FAILED, SendOutcome.IN_DOUBT)

    # Part 2 for one lead. False: another writer gave it a first reply or it was suppressed
    # (acknowledge_with_fallback returns False), or retention anonymized it after the read
    # (acknowledge_lead raises ValueError).
    def _fallback(self, lead_id: int) -> bool:
        try:
            return self.acknowledge_with_fallback(lead_id)
        except ValueError:
            log.info("lead %s: fallback skipped, lead anonymized", lead_id)
            return False

    # The steps of send(). subject_language overrides the lead's language for the subject (the
    # fallback acknowledgment is bilingual whatever the lead's language is).
    def _send(self, reply_id: int, *, subject_language: str | None) -> SendOutcome:
        with self._session_factory() as session:
            repo = Repository(session)
            # Step 1: fresh read; each Repository call commits, so no snapshot is kept.
            reply = repo.get_reply(reply_id)
            if reply is None:
                raise LookupError(f"reply {reply_id} not found")
            kind, status, lead_id = Action(reply.kind), ReplyStatus(reply.status), reply.lead_id
            # Step 2 (before the address check): the service's rule on top of the transition table.
            # First, so an unapproved draft is never moved (drafted -> failed would make it
            # sendable without approval) and a reply another worker is sending is never touched.
            if status not in SEND_FROM[kind]:
                if kind is Action.DRAFT and status is ReplyStatus.DRAFTED:
                    log.warning("reply %s: refused, draft not approved", reply_id)
                    return SendOutcome.REFUSED
                return SendOutcome.ALREADY_HANDLED
            lead = repo.get_lead(lead_id)
            if _no_address(lead):
                return self._refuse(repo, reply_id, status)
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
            try:
                result = self._sender.send(to=to, subject=subject, body=body)
            except Exception as e:
                # A sender must return a SendResult; one that raises after the claim may or may
                # not have sent, so it counts as in doubt. Class name only: the text may hold the address.
                log.error("reply %s: sender raised %s, outcome unknown", reply_id, type(e).__name__)
                result = SendResult(Outcome.TIMEOUT, type(e).__name__)
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
    # Best effort: a database error here is logged (class name only) and does not hide the send
    # outcome, which the reply row already records. The flag and the move are tried separately.
    def _update_lead(self, repo: Repository, lead_id: int, kind: Action, outcome: SendOutcome) -> None:
        if kind is Action.DRAFT or outcome not in (SendOutcome.SENT, SendOutcome.FAILED, SendOutcome.IN_DOUBT):
            return
        if outcome is SendOutcome.SENT:
            if kind is Action.AUTO_REPLY:
                self._move_lead(repo, lead_id, LeadStatus.AUTO_REPLIED)
            return
        try:
            repo.flag_for_review(lead_id)
        except SQLAlchemyError as e:
            log.error("lead %s: flagging for review failed (%s)", lead_id, type(e).__name__)
        if kind is Action.AUTO_REPLY:
            self._move_lead(repo, lead_id, LeadStatus.ACKNOWLEDGED)

    @staticmethod
    def _move_lead(repo: Repository, lead_id: int, status: LeadStatus) -> None:
        try:
            repo.set_lead_status(lead_id, status)
        except InvalidTransition:
            log.info("lead %s: not moved to %s, already moved by another writer", lead_id, status)
        except SQLAlchemyError as e:
            log.error("lead %s: moving to %s failed (%s)", lead_id, status, type(e).__name__)
