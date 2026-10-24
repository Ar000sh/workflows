"""ReplyService.sweep (A1 layer 4b, spec section 6 "Sweep", failure modes 6, 11b, 11c, 11d, P2).

The rule under test: the sweep resends only ACKNOWLEDGMENTS stuck in "sending" longer than
SENDING_TIMEOUT_MINUTES (10) and below MAX_SEND_ATTEMPTS (3); drafts and auto-replies in "sending"
are never touched. Acknowledgments and auto-replies left "drafted" (written, never claimed) for
longer than NO_REPLY_SWEEP_MINUTES are sent once; staff drafts never. Leads older than NO_REPLY_SWEEP_MINUTES (5) with no first reply and not
suppressed get the route-1 fallback acknowledgment. Two sweeps at once still send one email per
reply and per lead, and a second sweep after the first changes nothing.
A fake Sender stands in for SMTP; the clock is fixed at NOW so the ages are exact.
"""
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from lead_responder.db.models import LeadStatus, ReplyStatus
from lead_responder.db.session import make_session_factory
from lead_responder.limits import mailbox_key
from lead_responder.repository import InvalidTransition, Repository
from lead_responder.router import Action
from lead_responder.sender import Outcome, SendResult
from lead_responder.services.replies import STUCK_ERROR, ReplyService, SweepResult

NOW = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)
EMAIL = "anna.patient@example.com"
OTHER = "ben.patient@example.com"
STUCK = timedelta(minutes=11)  # older than the 10-minute timeout


# Records every send (thread-safe) and returns the scripted outcome; `delay` keeps a send in
# flight so two racing sweeps overlap.
class FakeSender:
    def __init__(self, outcome=Outcome.ACCEPTED, delay=0.0):
        self.outcome, self.delay = outcome, delay
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def send(self, *, to: str, subject: str, body: str) -> SendResult:
        with self._lock:
            self.calls.append({"to": to, "body": body})
        time.sleep(self.delay)
        return SendResult(self.outcome, "TimeoutError" if self.outcome is Outcome.TIMEOUT else "")


def make_service(engine, practice, sender) -> ReplyService:
    return ReplyService(
        session_factory=make_session_factory(engine), sender=sender, practice=practice, clock=lambda: NOW,
    )


# A lead created `age` before NOW (an hour by default: old enough for sweep part 2).
def make_lead(repo: Repository, email=EMAIL, age=timedelta(hours=1)):
    created = NOW - age
    lead, _ = repo.create_lead_if_new(
        name="Anna Test", email=email, phone=None, message="Ich habe Zahnschmerzen.",
        consent_at=created, now=created, mailbox_key=mailbox_key(email),
    )
    return lead


# A reply of `kind` left in "sending" by a send that never reported back: claimed `claimed_ago`
# before NOW, `attempts` claims so far (each earlier claim ended "failed" and was claimed again).
def stuck_reply(repo: Repository, kind=Action.ACKNOWLEDGMENT, email=EMAIL, claimed_ago=STUCK, attempts=1):
    lead = make_lead(repo, email)
    claimed = NOW - claimed_ago
    if kind is Action.DRAFT:  # staff write a draft after the lead got its acknowledgment
        repo.add_reply(lead.id, kind=Action.ACKNOWLEDGMENT, ai_body=None, body="Danke", status=ReplyStatus.SENT, now=claimed)
        reply = repo.add_reply(lead.id, kind=kind, ai_body=None, body="Entwurf", status=ReplyStatus.APPROVED, now=claimed)
    else:
        if kind is Action.AUTO_REPLY:
            repo.set_lead_status(lead.id, LeadStatus.CLASSIFIED)
        reply = repo.acknowledge_lead(lead.id, kind=kind, ai_body=None, body="Danke, wir melden uns.", now=claimed)
    for i in range(attempts):
        if i:
            repo.set_reply_status(reply.id, ReplyStatus.FAILED, now=claimed)
        repo.set_reply_status(reply.id, ReplyStatus.SENDING, now=claimed)
    return lead, reply


def test_stuck_acknowledgment_is_retried_after_timeout(session, engine, practice):
    repo = Repository(session)
    _, reply = stuck_reply(repo)
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(1, 0, 0)
    got = repo.get_reply(reply.id)
    assert (got.status, got.send_attempts, got.claimed_at, got.error) == ("sent", 2, NOW, None)
    assert [c["to"] for c in sender.calls] == [EMAIL]


# Spec: at the cap the acknowledgment stays "sending" for staff; it is only counted.
def test_acknowledgment_at_attempt_cap_is_left_for_staff(session, engine, practice):
    repo = Repository(session)
    _, reply = stuck_reply(repo, attempts=3)
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(0, 0, 1)
    got = repo.get_reply(reply.id)
    assert (got.status, got.send_attempts, got.claimed_at) == ("sending", 3, NOW - STUCK)
    assert sender.calls == []


# Failure mode 11c: a resent staff-approved medical reply would be a duplicate nobody approved.
def test_stuck_draft_is_never_touched(session, engine, practice):
    repo = Repository(session)
    _, reply = stuck_reply(repo, kind=Action.DRAFT)
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(0, 0, 0)
    assert (repo.get_reply(reply.id).status, repo.get_reply(reply.id).send_attempts) == ("sending", 1)
    assert sender.calls == []


def test_stuck_auto_reply_is_never_touched(session, engine, practice):
    repo = Repository(session)
    _, reply = stuck_reply(repo, kind=Action.AUTO_REPLY)
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(0, 0, 0)
    assert (repo.get_reply(reply.id).status, repo.get_reply(reply.id).send_attempts) == ("sending", 1)
    assert sender.calls == []


# The boundary: a claim EXACTLY 10 minutes old is not stuck yet. "Stuck" means strictly older than
# the timeout (the query uses claimed_at < now - 10 minutes), so the original send gets its full
# 10 minutes. A claim 9 minutes old is not stuck either.
def test_young_sending_reply_is_not_touched(session, engine, practice):
    repo = Repository(session)
    _, nine = stuck_reply(repo, email=EMAIL, claimed_ago=timedelta(minutes=9))
    _, ten = stuck_reply(repo, email=OTHER, claimed_ago=timedelta(minutes=10))
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(0, 0, 0)
    assert [repo.get_reply(r.id).status for r in (nine, ten)] == ["sending", "sending"]
    assert sender.calls == []


# Failure mode 6: the process died after saving the lead. A lead 6 minutes old with no reply gets
# the fallback acknowledgment and is flagged; one 4 minutes old may still be in its pipeline.
def test_lead_without_reply_gets_fallback_acknowledgment(session, engine, practice):
    repo = Repository(session)
    lead = make_lead(repo, email=EMAIL, age=timedelta(minutes=6))
    young = make_lead(repo, email=OTHER, age=timedelta(minutes=4))
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(0, 1, 0)
    got = repo.get_lead(lead.id)
    assert (got.status, got.needs_manual_review) == ("acknowledged", True)
    [reply] = repo.replies_for(lead.id)
    assert (reply.kind, reply.status) == ("acknowledgment", "sent")
    assert [c["to"] for c in sender.calls] == [EMAIL]
    assert repo.replies_for(young.id) == []


# The lead was moved to "acknowledged" but its acknowledgment was never written (a writer died in
# between). It gets one acknowledgment and keeps its status.
def test_acknowledged_lead_with_lost_acknowledgment_gets_one(session, engine, practice):
    repo = Repository(session)
    lead = make_lead(repo)
    repo.set_lead_status(lead.id, LeadStatus.ACKNOWLEDGED)
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(0, 1, 0)
    assert repo.get_lead(lead.id).status == "acknowledged"
    assert len(repo.replies_for(lead.id)) == 1
    assert [c["to"] for c in sender.calls] == [EMAIL]


# Probe P2: the per-address limit left this lead without email on purpose; the sweep must not
# send the very email the limit stopped.
def test_suppressed_lead_is_skipped(session, engine, practice):
    repo = Repository(session)
    lead = make_lead(repo)
    repo.suppress_lead(lead.id)
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(0, 0, 0)
    assert repo.replies_for(lead.id) == []
    assert sender.calls == []


# Review focus 2 and 4. Both sweeps read their lists BEFORE either acts (the barriers hold each
# sweep right after its read until the other has read too), so both try to act on the same stuck
# reply and the same lead. The conditional sending -> failed move lets one sweep resend the reply;
# acknowledge_lead's lock and first-reply check let one sweep acknowledge the lead.
def test_two_overlapping_sweeps_send_one_email_per_stuck_reply_and_per_lead(session, engine, practice, monkeypatch):
    repo = Repository(session)
    _, reply = stuck_reply(repo, email=EMAIL)
    lead = make_lead(repo, email=OTHER, age=timedelta(minutes=6))
    read_stuck, read_leads = threading.Barrier(2, timeout=10), threading.Barrier(2, timeout=10)
    original_stuck, original_leads = Repository.stuck_sending_replies, Repository.leads_without_first_reply

    def stuck_then_wait(self, **kwargs):
        rows = original_stuck(self, **kwargs)
        read_stuck.wait()
        return rows

    def leads_then_wait(self, **kwargs):
        rows = original_leads(self, **kwargs)
        read_leads.wait()
        return rows

    monkeypatch.setattr(Repository, "stuck_sending_replies", stuck_then_wait)
    monkeypatch.setattr(Repository, "leads_without_first_reply", leads_then_wait)
    sender = FakeSender(delay=0.2)
    service = make_service(engine, practice, sender)
    results, errors = [], []

    def worker():
        try:
            results.append(service.sweep())
        except Exception as e:  # surfaced by the assert below, not lost in the thread
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    monkeypatch.undo()
    assert errors == []
    assert sorted(c["to"] for c in sender.calls) == sorted([EMAIL, OTHER])
    assert sum(r.retried_acknowledgments for r in results) == 1
    assert sum(r.fallback_acknowledgments for r in results) == 1
    assert repo.get_reply(reply.id).send_attempts == 2
    assert len(repo.replies_for(lead.id)) == 1


# Failure mode 11d from the other side: a sweep acting on a list it read BEFORE another sweep
# resent the reply must not touch the new claim (it is in flight now, not stuck). The
# sending -> failed move re-checks "claimed before the cutoff" in its UPDATE, so the stale sweep
# gets InvalidTransition and sends nothing.
def test_stale_sweep_does_not_take_over_a_fresh_claim(session, engine, practice, monkeypatch):
    repo = Repository(session)
    _, reply = stuck_reply(repo)
    # Read in its own session, so the later re-reads in `session` cannot refresh these objects.
    with make_session_factory(engine)() as other:
        stale = Repository(other).stuck_sending_replies(
            kind=Action.ACKNOWLEDGMENT, claimed_before=NOW - timedelta(minutes=10), max_attempts=99,
        )
    timing_out = FakeSender(outcome=Outcome.TIMEOUT)
    assert make_service(engine, practice, timing_out).sweep() == SweepResult(1, 0, 0)
    assert repo.get_reply(reply.id).claimed_at == NOW  # resent, outcome unknown again
    monkeypatch.setattr(Repository, "stuck_sending_replies", lambda self, **kwargs: stale)
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(0, 0, 0)
    assert sender.calls == []
    got = repo.get_reply(reply.id)
    assert (got.status, got.send_attempts) == ("sending", 2)


def test_fail_stuck_reply_moves_only_a_claim_older_than_the_cutoff(session):
    repo = Repository(session)
    _, reply = stuck_reply(repo)
    with pytest.raises(InvalidTransition):
        repo.fail_stuck_reply(reply.id, claimed_before=NOW - STUCK, error=STUCK_ERROR)  # not strictly older
    got = repo.fail_stuck_reply(reply.id, claimed_before=NOW - timedelta(minutes=10), error=STUCK_ERROR)
    assert (got.status, got.error) == ("failed", "stuck: retried by sweep")
    with pytest.raises(InvalidTransition):  # no longer "sending"
        repo.fail_stuck_reply(reply.id, claimed_before=NOW, error=STUCK_ERROR)


def test_sweep_twice_is_idempotent(session, engine, practice):
    repo = Repository(session)
    _, reply = stuck_reply(repo, email=EMAIL)
    lead = make_lead(repo, email=OTHER, age=timedelta(minutes=6))
    sender = FakeSender()
    service = make_service(engine, practice, sender)
    assert service.sweep() == SweepResult(1, 1, 0)
    assert service.sweep() == SweepResult(0, 0, 0)
    assert len(sender.calls) == 2
    assert repo.get_reply(reply.id).send_attempts == 2
    assert len(repo.replies_for(lead.id)) == 1


# Retention blanked both leads (no address any more). Nothing is sent: the lead without a reply
# is not even selected, and the stuck acknowledgment is released and then refused by send, which
# leaves it "failed" (known not sent) as for any anonymized lead.
def test_sweep_skips_anonymized_leads(session, engine, practice):
    repo = Repository(session)
    _, reply = stuck_reply(repo, email=EMAIL)
    lead = make_lead(repo, email=OTHER, age=timedelta(minutes=6))
    repo.anonymize_older_than(cutoff=NOW + timedelta(days=1), now=NOW)
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(0, 0, 0)
    assert sender.calls == []
    assert repo.get_reply(reply.id).status == "failed"
    assert repo.replies_for(lead.id) == []


# --- part 1b: first replies written but never claimed (the writer died before the send) ---

STALE = timedelta(minutes=6)  # older than NO_REPLY_SWEEP_MINUTES (5)


# A first reply committed by acknowledge_lead `age` before NOW and never claimed: still "drafted".
def drafted_first_reply(repo: Repository, kind=Action.ACKNOWLEDGMENT, email=EMAIL, age=STALE):
    lead = make_lead(repo, email)
    if kind is Action.AUTO_REPLY:
        repo.set_lead_status(lead.id, LeadStatus.CLASSIFIED)
    return lead, repo.acknowledge_lead(lead.id, kind=kind, ai_body=None, body="Danke, wir melden uns.", now=NOW - age)


def test_stale_drafted_acknowledgment_is_sent_once(session, engine, practice):
    repo = Repository(session)
    lead, reply = drafted_first_reply(repo)
    sender = FakeSender()
    service = make_service(engine, practice, sender)
    assert service.sweep() == SweepResult(0, 0, 0, 1)
    assert service.sweep() == SweepResult(0, 0, 0, 0)
    assert repo.get_reply(reply.id).status == "sent"
    assert [c["to"] for c in sender.calls] == [EMAIL]


# send() also fixes the lead: a sent auto-reply makes it "auto_replied".
def test_stale_drafted_auto_reply_is_sent(session, engine, practice):
    repo = Repository(session)
    lead, reply = drafted_first_reply(repo, kind=Action.AUTO_REPLY)
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(0, 0, 0, 1)
    assert repo.get_reply(reply.id).status == "sent"
    assert repo.get_lead(lead.id).status == "auto_replied"


# Younger than the cutoff, or exactly at it (strict <): the original writer may still be sending.
def test_young_drafted_first_reply_is_not_touched(session, engine, practice):
    repo = Repository(session)
    _, young = drafted_first_reply(repo, age=timedelta(minutes=4))
    _, edge = drafted_first_reply(repo, email=OTHER, age=timedelta(minutes=5))
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(0, 0, 0, 0)
    assert sender.calls == []
    assert {repo.get_reply(young.id).status, repo.get_reply(edge.id).status} == {"drafted"}


# A staff draft waits for approval, however old: the sweep never sends it.
def test_stale_staff_draft_is_never_sent(session, engine, practice):
    repo = Repository(session)
    lead = make_lead(repo)
    repo.add_reply(lead.id, kind=Action.ACKNOWLEDGMENT, ai_body=None, body="Danke", status=ReplyStatus.SENT, now=NOW - STALE)
    draft = repo.add_reply(lead.id, kind=Action.DRAFT, ai_body=None, body="Entwurf", status=ReplyStatus.DRAFTED, now=NOW - STALE)
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(0, 0, 0, 0)
    assert sender.calls == []
    assert repo.get_reply(draft.id).status == "drafted"


# Both sweeps read the stale reply before either sends it; the claim lets one of them send.
def test_two_overlapping_sweeps_send_one_stale_drafted_reply_once(session, engine, practice, monkeypatch):
    repo = Repository(session)
    _, reply = drafted_first_reply(repo)
    read_both = threading.Barrier(2, timeout=10)
    original = Repository.stale_drafted_first_replies

    def read_then_wait(self, **kwargs):
        rows = original(self, **kwargs)
        read_both.wait()
        return rows

    monkeypatch.setattr(Repository, "stale_drafted_first_replies", read_then_wait)
    sender = FakeSender(delay=0.2)
    service = make_service(engine, practice, sender)
    results, errors = [], []

    def worker():
        try:
            results.append(service.sweep())
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    monkeypatch.undo()
    assert errors == []
    assert len(sender.calls) == 1
    assert sum(r.sent_stale_drafted for r in results) == 1
    assert repo.get_reply(reply.id).send_attempts == 1


def test_stale_drafted_reply_of_anonymized_lead_is_skipped(session, engine, practice):
    repo = Repository(session)
    _, reply = drafted_first_reply(repo)
    repo.anonymize_older_than(cutoff=NOW + timedelta(days=1), now=NOW)
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(0, 0, 0, 0)
    assert sender.calls == []
    assert repo.get_reply(reply.id).status == "drafted"
