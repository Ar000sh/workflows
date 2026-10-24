"""IntakeService (A1 layer 4b, spec sections 4, 6 "Intake", 7 and 8: rows 5, 7, 8, 11e, 11h, 11j, P6, P7).

The rules under test:
- receive() saves the lead (a duplicate gives None), counts the leads of the same mailbox
  INCLUDING the new one, and over the per-email limit suppresses it: no email, no AI.
- process() runs the pipeline in a worker thread under a deadline, with at most
  MAX_CONCURRENT_PIPELINES at once and at most MAX_AI_LEADS_PER_HOUR an hour. Over the cap, with
  no free slot, after the deadline or on any exception the lead gets the fixed DE+EN
  acknowledgment and is flagged. A run that outlives its deadline keeps its slot until it ends,
  and its late result writes nothing. process() never raises.
- The result is written in order: classification, status, first reply, draft, awaiting_approval,
  then the send. No database transaction is open while the pipeline runs.
A fake pipeline (a callable returning a hand-built PipelineResult) stands in for the AI and a
fake Sender for SMTP. Deadlines are fractions of a second so the suite stays fast.
"""
import logging
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from lead_responder import config, templates
from lead_responder.api.schemas import LeadIn
from lead_responder.db.models import Lead
from lead_responder.models import Category, Classification, Risk, Urgency
from lead_responder.pipeline import GeneratedReply, PipelineResult
from lead_responder.repository import Repository
from lead_responder.router import Action, decide, downgrade_failed_auto_reply
from lead_responder.sender import Outcome, SendResult
from lead_responder.services.intake import IntakeService, Received
from lead_responder.services.replies import ReplyService

NOW = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)
EMAIL = "anna.patient@example.com"
FAQ = "BrightSmile FAQ"
LLM = object()  # the pipeline is fake, so the client is only passed through


# A clock the test can move: receive() and process() read "now" from it.
class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


# Hands out one new Session per call (like sessionmaker) and remembers each one, so the fake
# pipeline can check that none of them is inside a transaction while it runs.
class RecordingSessionFactory:
    def __init__(self, engine):
        self.engine = engine
        self.sessions: list[Session] = []
        self._lock = threading.Lock()

    def __call__(self) -> Session:
        s = Session(self.engine, expire_on_commit=False)
        with self._lock:
            self.sessions.append(s)
        return s


# Records every send (thread-safe) and accepts it.
class FakeSender:
    def __init__(self):
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def send(self, *, to: str, subject: str, body: str) -> SendResult:
        with self._lock:
            self.calls.append({"to": to, "subject": subject, "body": body})
        return SendResult(Outcome.ACCEPTED, "")


# Stands in for process_inquiry. Records each call's arguments; `during` runs inside the call
# (to look at the database or change it mid-run); `block` holds the FIRST call until the event is
# set; `delay` keeps every call in flight; `raises` makes it crash. `started`/`finished` tell the
# test where the run is.
class FakePipeline:
    def __init__(self, result=None, *, delay=0.0, raises=None, block=None, during=None):
        self.result, self.delay, self.raises, self.block, self.during = result, delay, raises, block, during
        self.calls: list[dict] = []
        self.started = threading.Event()
        self.finished = threading.Event()
        self._lock = threading.Lock()

    def __call__(self, **kwargs) -> PipelineResult:
        with self._lock:
            self.calls.append(kwargs)
            first = len(self.calls) == 1
        self.started.set()
        try:
            if self.during is not None:
                self.during(kwargs)
            if self.block is not None and first:
                self.block.wait(5)
            time.sleep(self.delay)
            if self.raises is not None:
                raise self.raises
            return self.result
        finally:
            self.finished.set()


def classification(category=Category.APPOINTMENT, risk=Risk.ROUTINE, language="de") -> Classification:
    return Classification(category=category, risk=risk, urgency=Urgency.NORMAL, language=language, reason="Grund")


def routine_result() -> PipelineResult:
    c = classification()
    return PipelineResult(c, decide(c), (GeneratedReply(Action.AUTO_REPLY, "Hallo Anna, gern!", "Hallo {name}, gern!"),), (), 0.01)


def sensitive_result() -> PipelineResult:
    c = classification(Category.PAIN_EMERGENCY, Risk.SENSITIVE)
    replies = (
        GeneratedReply(Action.ACKNOWLEDGMENT, "Danke, wir melden uns. 112", None),
        GeneratedReply(Action.DRAFT, "Entwurf fuer Anna", "Entwurf fuer {name}", ("warning",)),
    )
    return PipelineResult(c, decide(c), replies, (), 0.02)


def classifier_failure_result() -> PipelineResult:
    return PipelineResult(None, decide(None), (GeneratedReply(Action.ACKNOWLEDGMENT, "Hallo / Hello", None),), (), 0.0)


def unsupported_language_result() -> PipelineResult:
    c = classification(language="tr")
    return PipelineResult(c, decide(c), (GeneratedReply(Action.ACKNOWLEDGMENT, "Hallo / Hello", None),), (), 0.01)


def lead_in(email=EMAIL, message="Ich moechte einen Termin.", name="Anna Test") -> LeadIn:
    return LeadIn(name=name, email=email, phone=None, message=message, consent=True)


@pytest.fixture
def factory(engine, session):  # `session` truncates the tables first
    f = RecordingSessionFactory(engine)
    yield f
    for s in f.sessions:
        s.close()


# Builds services and shuts each one down at the end of the test, so an orphaned run from one
# test has finished before the next test truncates the tables.
@pytest.fixture
def make_intake(factory, practice):
    built: list[IntakeService] = []

    def build(pipeline, *, clock=None, sender=None):
        clock = clock or Clock()
        sender = sender or FakeSender()
        replies = ReplyService(session_factory=factory, sender=sender, practice=practice, clock=clock)
        intake = IntakeService(
            session_factory=factory, replies=replies, llm=LLM, practice=practice, faq=FAQ,
            clock=clock, pipeline=pipeline,
        )
        built.append(intake)
        return intake, sender

    yield build
    for intake in built:
        intake.shutdown()


# Waits (bounded) until every pipeline slot of the service is free again. The slot is given back
# in the run's done-callback, a moment after the fake pipeline's last line.
def wait_for_free_slots(intake: IntakeService, timeout=2.0) -> None:
    deadline = time.monotonic() + timeout
    while intake._slots._value < config.MAX_CONCURRENT_PIPELINES:
        assert time.monotonic() < deadline, "pipeline slot was never released"
        time.sleep(0.01)


def receive_and_process(intake: IntakeService, lead: LeadIn | None = None) -> int:
    received = intake.receive(lead or lead_in())
    assert received is not None and received.run_pipeline is True
    intake.process(received.lead_id)
    return received.lead_id


def assert_fallback_only(session, lead_id, sender, practice):
    repo = Repository(session)
    lead = repo.get_lead(lead_id)
    assert (lead.status, lead.needs_manual_review, lead.category) == ("acknowledged", True, None)
    [reply] = repo.replies_for(lead_id)
    assert (reply.kind, reply.status) == ("acknowledgment", "sent")
    [call] = sender.calls
    assert call["subject"] == templates.subject("de+en", practice)
    assert call["body"].startswith("Hallo") and "Hello" in call["body"] and "112" in call["body"]


def test_routine_lead_gets_auto_reply_and_becomes_auto_replied(session, make_intake, practice):
    pipeline = FakePipeline(routine_result())
    intake, sender = make_intake(pipeline)
    lead_id = receive_and_process(intake)
    [call] = pipeline.calls
    assert call == {"llm": LLM, "practice": practice, "faq": FAQ, "name": "Anna Test",
                    "message": "Ich moechte einen Termin.", "now": NOW}
    repo = Repository(session)
    lead = repo.get_lead(lead_id)
    assert (lead.status, lead.category, lead.risk, lead.needs_manual_review) == ("auto_replied", "appointment", "routine", False)
    [reply] = repo.replies_for(lead_id)
    assert (reply.kind, reply.status, reply.body, reply.ai_body) == ("auto_reply", "sent", "Hallo Anna, gern!", "Hallo {name}, gern!")
    assert [c["to"] for c in sender.calls] == [EMAIL]


def test_sensitive_lead_gets_acknowledgment_and_draft_and_awaits_approval(session, make_intake):
    intake, sender = make_intake(FakePipeline(sensitive_result()))
    lead_id = receive_and_process(intake)
    repo = Repository(session)
    assert repo.get_lead(lead_id).status == "awaiting_approval"
    ack, draft = repo.replies_for(lead_id)
    assert (ack.kind, ack.status) == ("acknowledgment", "sent")
    assert (draft.kind, draft.status, draft.body, draft.ai_body) == ("draft", "drafted", "Entwurf fuer Anna", "Entwurf fuer {name}")
    assert [c["body"] for c in sender.calls] == ["Danke, wir melden uns. 112"]  # the draft waits for staff


def test_classifier_failure_route_sends_acknowledgment_only_and_flags(session, make_intake):
    intake, sender = make_intake(FakePipeline(classifier_failure_result()))
    lead_id = receive_and_process(intake)
    repo = Repository(session)
    lead = repo.get_lead(lead_id)
    assert (lead.status, lead.needs_manual_review, lead.category) == ("acknowledged", True, None)
    [reply] = repo.replies_for(lead_id)
    assert (reply.kind, reply.status) == ("acknowledgment", "sent")
    assert len(sender.calls) == 1


def test_unsupported_language_route_sends_combined_acknowledgment_no_draft(session, make_intake):
    intake, sender = make_intake(FakePipeline(unsupported_language_result()))
    lead_id = receive_and_process(intake)
    repo = Repository(session)
    lead = repo.get_lead(lead_id)
    assert (lead.status, lead.language, lead.language_unsupported, lead.needs_manual_review) == ("acknowledged", "tr", True, True)
    [reply] = repo.replies_for(lead_id)
    assert (reply.kind, reply.status) == ("acknowledgment", "sent")
    assert [c["body"] for c in sender.calls] == ["Hallo / Hello"]


# The guardrails rejected the auto-reply: the pipeline downgrades the lead to sensitive and keeps
# the rejected text as a staff draft next to the acknowledgment (router.downgrade_failed_auto_reply).
def test_downgraded_routine_lead_gets_acknowledgment_and_rejected_text_as_draft(session, make_intake):
    c = classification()
    decision = downgrade_failed_auto_reply(decide(c))
    replies = (
        GeneratedReply(Action.ACKNOWLEDGMENT, "Danke, wir melden uns.", None),
        GeneratedReply(Action.DRAFT, "Abgelehnter Text", "Abgelehnter Text", ("too long",)),
    )
    intake, sender = make_intake(FakePipeline(PipelineResult(c, decision, replies, (), 0.01)))
    lead_id = receive_and_process(intake)
    repo = Repository(session)
    lead = repo.get_lead(lead_id)
    assert (lead.status, lead.risk, lead.needs_manual_review) == ("awaiting_approval", "routine", True)
    ack, draft = repo.replies_for(lead_id)
    assert (ack.kind, ack.status, draft.kind, draft.status) == ("acknowledgment", "sent", "draft", "drafted")
    assert [c["body"] for c in sender.calls] == ["Danke, wir melden uns."]


# Route 4: routine, AUTO_REPLY_MODE=outside_hours and the practice is open. The patient gets the
# fixed acknowledgment, staff get an AI draft to review; the lead is not flagged.
def test_routine_lead_in_opening_hours_mode_gets_acknowledgment_and_draft(session, make_intake):
    c = classification()
    decision = decide(c, auto_reply_mode="outside_hours", practice_open=True)
    replies = (
        GeneratedReply(Action.ACKNOWLEDGMENT, "Danke, wir melden uns.", None),
        GeneratedReply(Action.DRAFT, "Entwurf", "Entwurf"),
    )
    intake, sender = make_intake(FakePipeline(PipelineResult(c, decision, replies, (), 0.01)))
    lead_id = receive_and_process(intake)
    repo = Repository(session)
    lead = repo.get_lead(lead_id)
    assert (lead.status, lead.needs_manual_review) == ("awaiting_approval", False)
    ack, draft = repo.replies_for(lead_id)
    assert (ack.kind, ack.status, draft.kind, draft.status) == ("acknowledgment", "sent", "draft", "drafted")
    assert len(sender.calls) == 1


# Fix round 1: a first reply committed by acknowledge_lead must still be sent when a later write
# fails AND the anonymized check fails too (database down): otherwise it would sit "drafted" with
# nobody sending it. Exactly one email, the lead flagged, process() does not raise.
def test_first_reply_is_still_sent_when_a_later_write_and_the_anonymized_check_fail(session, make_intake, monkeypatch):
    def down(*args, **kwargs):
        raise OperationalError("database", {}, Exception("database down"))

    monkeypatch.setattr(Repository, "add_reply", down)  # the draft write after acknowledge_lead
    monkeypatch.setattr(IntakeService, "_anonymized", down)
    intake, sender = make_intake(FakePipeline(sensitive_result()))
    lead_id = receive_and_process(intake)
    repo = Repository(session)
    [ack] = repo.replies_for(lead_id)
    assert (ack.kind, ack.status) == ("acknowledgment", "sent")
    assert [c["body"] for c in sender.calls] == ["Danke, wir melden uns. 112"]
    assert repo.get_lead(lead_id).needs_manual_review is True


def test_duplicate_returns_none_and_sends_nothing(session, make_intake):
    pipeline = FakePipeline(routine_result())
    intake, sender = make_intake(pipeline)
    first = intake.receive(lead_in())
    assert first == Received(first.lead_id, True)
    assert intake.receive(lead_in()) is None
    assert session.scalar(select(func.count()).select_from(Lead)) == 1
    assert sender.calls == [] and pipeline.calls == []


# Spec row 11j: more than MAX_LEADS_PER_EMAIL_HOUR (3) in the last hour, counted with the new lead.
def test_fourth_lead_in_an_hour_is_suppressed_no_email_no_pipeline_call(session, make_intake):
    clock = Clock()
    pipeline = FakePipeline(routine_result())
    intake, sender = make_intake(pipeline, clock=clock)
    allowed = []
    for i, minutes_ago in enumerate((50, 30, 10)):
        clock.now = NOW - timedelta(minutes=minutes_ago)
        allowed.append(intake.receive(lead_in(message=f"Frage {i}")))
    assert [r.run_pipeline for r in allowed] == [True, True, True]
    clock.now = NOW
    fourth = intake.receive(lead_in(message="Frage 4"))
    assert fourth.run_pipeline is False
    lead = Repository(session).get_lead(fourth.lead_id)
    assert (lead.status, lead.send_suppressed, lead.needs_manual_review) == ("acknowledged", True, True)
    assert Repository(session).replies_for(fourth.lead_id) == []
    assert sender.calls == [] and pipeline.calls == []


# More than MAX_LEADS_PER_EMAIL_DAY (10) in the last day, never more than one an hour.
def test_eleventh_in_a_day(session, make_intake):
    clock = Clock()
    intake, sender = make_intake(FakePipeline(routine_result()), clock=clock)
    results = []
    for i in range(11):
        clock.now = NOW - timedelta(hours=2 * (10 - i))  # 20 h ago ... now, two hours apart
        results.append(intake.receive(lead_in(message=f"Frage {i}")))
    assert [r.run_pipeline for r in results] == [True] * 10 + [False]
    assert Repository(session).get_lead(results[-1].lead_id).send_suppressed is True
    assert sender.calls == []


# Probe P3: "+tag" aliases and letter case are one mailbox, so they share one counter.
def test_plus_alias_counts_toward_the_same_limit(session, make_intake):
    intake, sender = make_intake(FakePipeline(routine_result()))
    emails = ["anna.patient+1@example.com", "Anna.Patient+2@example.com", "anna.patient@example.com", "anna.patient+x@example.com"]
    results = [intake.receive(lead_in(email=e, message=f"Frage {i}")) for i, e in enumerate(emails)]
    assert [r.run_pipeline for r in results] == [True, True, True, False]


# Spec row 11h: above MAX_AI_LEADS_PER_HOUR leads (all addresses, the lead included) in the last
# hour, the lead gets the fixed acknowledgment and the AI is never called.
def test_global_ai_cap_sends_fixed_acknowledgment_without_pipeline_call(session, make_intake, practice, monkeypatch):
    monkeypatch.setattr(config, "MAX_AI_LEADS_PER_HOUR", 2)
    pipeline = FakePipeline(routine_result())
    intake, sender = make_intake(pipeline)
    for e in ("ben@example.com", "carla@example.com"):
        assert intake.receive(lead_in(email=e)).run_pipeline is True
    lead_id = receive_and_process(intake)  # the third lead this hour
    assert pipeline.calls == []
    assert_fallback_only(session, lead_id, sender, practice)


# Probe P6: with every slot taken (one slot here, held by a run blocked in the pipeline) the next
# lead gets the fixed acknowledgment at once, without a pipeline call.
def test_no_free_pipeline_slot_sends_fixed_acknowledgment_only(session, make_intake, practice, monkeypatch):
    monkeypatch.setattr(config, "MAX_CONCURRENT_PIPELINES", 1)
    monkeypatch.setattr(config, "PIPELINE_DEADLINE_SECONDS", 5)
    release = threading.Event()
    pipeline = FakePipeline(routine_result(), block=release)
    intake, sender = make_intake(pipeline)
    first = intake.receive(lead_in(email="ben@example.com"))
    worker = threading.Thread(target=intake.process, args=(first.lead_id,))
    worker.start()
    try:
        assert pipeline.started.wait(2)
        second = intake.receive(lead_in())
        intake.process(second.lead_id)
        assert len(pipeline.calls) == 1  # only the first lead reached the pipeline
        assert_fallback_only(session, second.lead_id, sender, practice)
    finally:
        release.set()
        worker.join(5)
    assert Repository(session).get_lead(first.lead_id).status == "auto_replied"


# Spec rows 8 and 11e: the pipeline takes 0.6 s against a 0.2 s deadline. process() gives up at
# the deadline and sends the fallback; when the run ends later its result is thrown away: still
# one acknowledgment, one email, and the lead is not classified by the late result.
def test_pipeline_over_deadline_sends_fallback_and_late_result_writes_nothing(session, make_intake, practice, monkeypatch):
    monkeypatch.setattr(config, "PIPELINE_DEADLINE_SECONDS", 0.2)
    pipeline = FakePipeline(routine_result(), delay=0.6)
    intake, sender = make_intake(pipeline)
    started = time.monotonic()
    lead_id = receive_and_process(intake)
    waited = time.monotonic() - started
    assert_fallback_only(session, lead_id, sender, practice)
    assert waited < pipeline.delay  # did not wait for the slow run
    assert pipeline.finished.wait(2)
    wait_for_free_slots(intake)
    assert_fallback_only(session, lead_id, sender, practice)


# Spec row 7: the pipeline raises. The fallback goes out, the lead is flagged, and the log holds
# the exception's class name only (its text may carry personal data).
def test_any_pipeline_exception_sends_fallback_and_flags(session, make_intake, practice, caplog):
    pipeline = FakePipeline(raises=RuntimeError(f"broke on {EMAIL}"))
    intake, sender = make_intake(pipeline)
    with caplog.at_level(logging.DEBUG):
        lead_id = receive_and_process(intake)
    assert_fallback_only(session, lead_id, sender, practice)
    assert "RuntimeError" in caplog.text and EMAIL not in caplog.text


# Probe P6: a run past its deadline keeps its slot until it really ends, otherwise orphans could
# pile up beyond MAX_CONCURRENT_PIPELINES. One slot: lead A's run is orphaned at the deadline,
# lead B finds no slot, and only after A's run has finished does lead C reach the pipeline.
def test_orphaned_run_keeps_its_pipeline_slot_until_it_finishes(session, make_intake, practice, monkeypatch):
    monkeypatch.setattr(config, "MAX_CONCURRENT_PIPELINES", 1)
    monkeypatch.setattr(config, "PIPELINE_DEADLINE_SECONDS", 0.2)
    release = threading.Event()
    pipeline = FakePipeline(routine_result(), block=release)
    intake, sender = make_intake(pipeline)
    try:
        receive_and_process(intake, lead_in(email="a@example.com"))  # orphaned after 0.2 s
        b = intake.receive(lead_in(email="b@example.com"))
        intake.process(b.lead_id)
        assert len(pipeline.calls) == 1
        assert Repository(session).get_lead(b.lead_id).needs_manual_review is True
    finally:
        release.set()
    assert pipeline.finished.wait(2)
    wait_for_free_slots(intake)
    c = intake.receive(lead_in(email="c@example.com"))
    intake.process(c.lead_id)
    assert len(pipeline.calls) == 2
    assert Repository(session).get_lead(c.lead_id).status == "auto_replied"


# Spec row 5: the database fails while saving. The error reaches the caller (the API answers
# 503) and nothing is sent or run.
def test_database_error_on_save_raises_and_sends_nothing(session, make_intake, monkeypatch):
    def down(self, **kwargs):
        raise OperationalError("INSERT INTO leads", {}, Exception("database down"))

    monkeypatch.setattr(Repository, "create_lead_if_new", down)
    pipeline = FakePipeline(routine_result())
    intake, sender = make_intake(pipeline)
    with pytest.raises(OperationalError):
        intake.receive(lead_in())
    assert sender.calls == [] and pipeline.calls == []


# Retention blanks the lead while the AI runs. The result is personal data about a patient who
# must be forgotten: it is discarded without an error (nothing written, nothing sent, no ERROR log).
def test_anonymized_lead_result_is_discarded_without_error(session, engine, make_intake, caplog):
    def anonymize(_kwargs):
        with Session(engine) as s:
            Repository(s).anonymize_older_than(cutoff=NOW + timedelta(days=1), now=NOW)

    intake, sender = make_intake(FakePipeline(routine_result(), during=anonymize))
    with caplog.at_level(logging.INFO):
        lead_id = receive_and_process(intake)
    repo = Repository(session)
    lead = repo.get_lead(lead_id)
    assert lead.anonymized_at is not None and (lead.status, lead.category) == ("new", None)
    assert repo.replies_for(lead_id) == [] and sender.calls == []
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# Spec row 11e from the in-time side: while the AI runs, another writer (the sweep) gives the lead
# its fallback acknowledgment. The result then finds the lead moved on and is discarded: one
# acknowledgment, one email, the lead not classified.
def test_result_after_another_writer_acknowledged_is_discarded(session, engine, make_intake, practice):
    other_sender = FakeSender()
    holder = {}

    def sweep_wins(_kwargs):
        other = ReplyService(session_factory=lambda: Session(engine, expire_on_commit=False),
                             sender=other_sender, practice=practice, clock=lambda: NOW)
        assert other.acknowledge_with_fallback(holder["lead_id"]) is True

    pipeline = FakePipeline(routine_result(), during=sweep_wins)
    intake, sender = make_intake(pipeline)
    received = intake.receive(lead_in())
    holder["lead_id"] = received.lead_id
    intake.process(received.lead_id)
    repo = Repository(session)
    lead = repo.get_lead(received.lead_id)
    assert (lead.status, lead.category) == ("acknowledged", None)
    [reply] = repo.replies_for(received.lead_id)
    assert reply.kind == "acknowledgment"
    assert len(other_sender.calls) == 1 and sender.calls == []


# Spec rule (probe P6): no transaction or row lock stays open while the AI runs. Inside the fake
# pipeline: no service session is in a transaction and no database connection sits "idle in
# transaction" (a lock or snapshot held across the run would show up there).
def test_no_session_is_in_a_transaction_while_the_pipeline_runs(session, engine, factory, make_intake):
    seen = {}

    def look(_kwargs):
        seen["open"] = any(s.in_transaction() for s in factory.sessions)
        with engine.connect() as conn:
            seen["idle"] = conn.scalar(text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND state LIKE 'idle in transaction%' AND pid <> pg_backend_pid()"
            ))

    intake, _ = make_intake(FakePipeline(routine_result(), during=look))
    receive_and_process(intake)
    assert seen == {"open": False, "idle": 0}
