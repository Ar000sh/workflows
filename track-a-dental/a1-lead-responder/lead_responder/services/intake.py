"""IntakeService: takes a validated lead from the form to its first email (spec section 6 "Intake").

Two steps, so the form gets its answer fast:
  receive(lead)   cheap local work while the form waits: save the lead (a duplicate stops here),
                  then the per-email limit. Over it the lead is suppressed: no email, no AI.
  process(id)     the slow part, run in the background after the form has its answer:
    1. the global AI cap and a free pipeline slot; without them -> the fixed acknowledgment
    2. the pipeline (layers 1-3) in a worker thread under a deadline; too slow or crashed ->
       the fixed acknowledgment
    3. write the result: classification, status, first reply, draft, awaiting_approval
    4. send the acknowledgment or auto-reply through ReplyService (claim before send)
"The fixed acknowledgment" is ReplyService.acknowledge_with_fallback: the DE+EN route-1 template
with the emergency numbers, and the lead flagged for staff. process() never raises: it runs in a
background task, where an exception would only vanish into a log.

Who may write a lead's result is decided by the repository's conditional writes, not here: a
result that finds the lead moved on (another writer gave it its first reply) gets
InvalidTransition and is discarded. No database transaction is open while the pipeline runs.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy.orm import Session

from lead_responder import config, limits
from lead_responder.api.schemas import LeadIn
from lead_responder.db.models import LeadStatus, ReplyStatus
from lead_responder.pipeline import PipelineResult, process_inquiry
from lead_responder.practice import Practice
from lead_responder.repository import InvalidTransition, Repository
from lead_responder.router import Action
from lead_responder.services.replies import ReplyService
from shared.llm import LLMClient

log = logging.getLogger(__name__)


# What receive() decided. run_pipeline=False: the lead was suppressed by the per-email limit and
# the caller must not start process() for it.
@dataclass(frozen=True)
class Received:
    lead_id: int
    run_pipeline: bool


class IntakeService:
    # session_factory makes one short-lived Session per call; clock gives "now" (timezone-aware);
    # pipeline is process_inquiry, replaced by a fake in tests. llm, practice and faq are built
    # once at app start and passed through to the pipeline.
    # The semaphore counts runs that are really still going, the executor gives each one a thread.
    # Both are sized MAX_CONCURRENT_PIPELINES, so a submitted run never waits in the pool's queue.
    def __init__(
        self, *, session_factory: Callable[[], Session], replies: ReplyService, llm: LLMClient,
        practice: Practice, faq: str, clock: Callable[[], datetime],
        pipeline: Callable[..., PipelineResult] = process_inquiry,
    ):
        self._session_factory = session_factory
        self._replies = replies
        self._llm = llm
        self._practice = practice
        self._faq = faq
        self._clock = clock
        self._pipeline = pipeline
        self._slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_PIPELINES)
        self._executor = ThreadPoolExecutor(
            max_workers=config.MAX_CONCURRENT_PIPELINES, thread_name_prefix="pipeline",
        )

    # Saves the lead and applies the per-email limit. None = a duplicate (same address and message
    # within 2 minutes): nothing else happens. The count runs AFTER the save and includes the new
    # lead, so two posts at the same moment each see the other (probe P7) and the limit errs on the
    # safe side. A database error propagates (the API answers 503); nothing has been sent then.
    def receive(self, lead: LeadIn) -> Received | None:
        now = self._clock()
        key = limits.mailbox_key(lead.email)
        with self._session_factory() as session:
            repo = Repository(session)
            row, created = repo.create_lead_if_new(
                name=lead.name, email=lead.email, phone=lead.phone, message=lead.message,
                consent_at=now, now=now, mailbox_key=key,
            )
            if not created:
                log.info("lead %s: duplicate, ignored", row.id)
                return None
            lead_id = row.id
            in_hour = repo.count_leads_for_mailbox_since(key, now - timedelta(hours=1))
            in_day = repo.count_leads_for_mailbox_since(key, now - timedelta(days=1))
            if in_hour > config.MAX_LEADS_PER_EMAIL_HOUR or in_day > config.MAX_LEADS_PER_EMAIL_DAY:
                repo.suppress_lead(lead_id)
                log.warning("lead %s: per-email limit exceeded, suppressed", lead_id)
                return Received(lead_id, False)
        return Received(lead_id, True)

    # The background part for one lead (module docstring, steps 1-4). Never raises: every failure
    # ends in the fixed acknowledgment or, when the lead has moved on, in nothing at all.
    def process(self, lead_id: int) -> None:
        try:
            try:
                result = self._run_pipeline(lead_id)
            except Exception as e:  # reading the lead or the cap failed
                log.error("lead %s: preparing the pipeline failed (%s)", lead_id, type(e).__name__)
                result = None
            if result is None:
                self._fallback(lead_id)
                return
            first_replies: list[int] = []
            try:
                self._write_result(lead_id, result, first_replies)
            except InvalidTransition:
                # Discard: another writer (the sweep, a fallback) got to the lead first.
                log.info("lead %s: result discarded, lead already moved on", lead_id)
            except Exception as e:
                if self._known_anonymized(lead_id):
                    # Discard: retention blanked the lead during the run; its result is personal data.
                    log.info("lead %s: result discarded, lead anonymized", lead_id)
                else:
                    log.error("lead %s: writing the result failed (%s)", lead_id, type(e).__name__)
                    self._fallback(lead_id)
            finally:
                # A first reply this run committed is ALWAYS sent, whatever failed after it: it is
                # a "drafted" row nobody else would send soon (the sweep only picks it up after
                # NO_REPLY_SWEEP_MINUTES). The claim makes a second attempt harmless, send()
                # refuses an anonymized lead, and _send never raises.
                self._send(lead_id, first_replies)
        except Exception as e:
            log.error("lead %s: processing failed (%s)", lead_id, type(e).__name__)

    # Waits for running pipelines to finish and stops the worker threads (app shutdown, tests).
    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)

    # Steps 1 and 2. Returns the result, or None when the lead must get the fixed acknowledgment.
    # The lead is read and the cap counted in a session that is closed before the pipeline starts:
    # no transaction, row lock or pooled connection is held while the AI runs.
    def _run_pipeline(self, lead_id: int) -> PipelineResult | None:
        now = self._clock()
        with self._session_factory() as session:
            repo = Repository(session)
            lead = repo.get_lead(lead_id)
            if lead is None:
                raise LookupError(f"lead {lead_id} not found")
            name, message = lead.name, lead.message
            recent = repo.count_leads_since(now - timedelta(hours=1))
        # The global cap bounds API spend; the count includes this lead.
        if recent > config.MAX_AI_LEADS_PER_HOUR:
            log.warning("lead %s: global AI cap reached, fixed acknowledgment", lead_id)
            return None
        # Slot: non-blocking, so a busy server answers with the fixed acknowledgment at once
        # instead of queueing leads behind slow runs.
        if not self._slots.acquire(blocking=False):
            log.warning("lead %s: no free pipeline slot, fixed acknowledgment", lead_id)
            return None
        try:
            future = self._executor.submit(
                self._pipeline, llm=self._llm, practice=self._practice, faq=self._faq,
                name=name, message=message, now=now,
            )
        except BaseException:
            self._slots.release()
            raise
        # Orphan: Python cannot cancel a running thread, so a run past its deadline goes on in the
        # background. Its slot is released only when it really ends (done-callback), so orphans
        # cannot pile up beyond MAX_CONCURRENT_PIPELINES threads and database connections.
        future.add_done_callback(lambda _done: self._slots.release())
        try:
            # Deadline: the pipeline's retries can take minutes; the patient must not wait for them.
            # After it, nobody reads this future again, so a late result is never written.
            return future.result(timeout=config.PIPELINE_DEADLINE_SECONDS)
        except TimeoutError:
            log.warning("lead %s: pipeline deadline expired, fixed acknowledgment", lead_id)
        except Exception as e:
            log.error("lead %s: pipeline raised %s, fixed acknowledgment", lead_id, type(e).__name__)
        return None

    # Step 3, in this order: classification (conditional: only while the lead is "new"), then
    # "classified" when the AI classified it in a supported language (routes 1 and 2 stay "new"
    # until acknowledge_lead moves them to "acknowledged"), then each reply, then
    # "awaiting_approval" when a draft was added. First replies (acknowledgment, auto-reply) go
    # through acknowledge_lead, which lets only one writer give the lead its first reply; their
    # ids are collected for step 4. Drafts are stored "drafted" for staff; their guardrail warnings
    # are not stored (4d recomputes them at approval). Each call is its own short transaction.
    def _write_result(self, lead_id: int, result: PipelineResult, first_replies: list[int]) -> None:
        now = self._clock()
        decision = result.decision
        with self._session_factory() as session:
            repo = Repository(session)
            repo.set_classification(lead_id, classification=result.classification, decision=decision)
            if result.classification is not None and not decision.language_unsupported:
                repo.set_lead_status(lead_id, LeadStatus.CLASSIFIED)
            drafted = False
            for reply in result.replies:
                if reply.kind is Action.DRAFT:
                    repo.add_reply(
                        lead_id, kind=Action.DRAFT, ai_body=reply.ai_body, body=reply.body,
                        status=ReplyStatus.DRAFTED, now=now,
                    )
                    drafted = True
                else:
                    row = repo.acknowledge_lead(
                        lead_id, kind=reply.kind, ai_body=reply.ai_body, body=reply.body, now=now,
                        needs_manual_review=decision.needs_manual_review,
                    )
                    first_replies.append(row.id)
            if drafted:
                repo.set_lead_status(lead_id, LeadStatus.AWAITING_APPROVAL)

    # Step 4: ReplyService claims and sends each first reply and fixes the lead's status after.
    # A send that raises (database down) flags the lead so staff check it.
    def _send(self, lead_id: int, reply_ids: list[int]) -> None:
        for reply_id in reply_ids:
            try:
                self._replies.send(reply_id)
            except Exception as e:
                log.error("reply %s: send raised %s", reply_id, type(e).__name__)
                self._flag(lead_id)

    # The fixed acknowledgment (flags the lead when it wins). False = the lead already has a first
    # reply (another writer, or this run before a later write failed): flag it, staff must look.
    # An anonymized lead (ValueError) gets nothing. Never raises.
    def _fallback(self, lead_id: int) -> None:
        try:
            if not self._replies.acknowledge_with_fallback(lead_id):
                self._flag(lead_id)
        except ValueError:
            log.info("lead %s: fallback skipped, lead anonymized", lead_id)
        except Exception as e:
            log.error("lead %s: fallback acknowledgment failed (%s)", lead_id, type(e).__name__)

    def _flag(self, lead_id: int) -> None:
        try:
            with self._session_factory() as session:
                Repository(session).flag_for_review(lead_id)
        except Exception as e:
            log.error("lead %s: flagging for review failed (%s)", lead_id, type(e).__name__)

    # True if retention anonymized the lead. If the check itself fails (database down) the answer
    # is False: the caller then tries the fallback and the send, which refuse an anonymized lead
    # themselves, rather than dropping a reply on an unanswered question.
    def _known_anonymized(self, lead_id: int) -> bool:
        try:
            return self._anonymized(lead_id)
        except Exception as e:
            log.error("lead %s: anonymized check failed (%s)", lead_id, type(e).__name__)
            return False

    def _anonymized(self, lead_id: int) -> bool:
        with self._session_factory() as session:
            lead = Repository(session).get_lead(lead_id)
            return lead is not None and lead.anonymized_at is not None
