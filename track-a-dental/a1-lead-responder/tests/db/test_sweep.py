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


# Never resent, but its lead is flagged so staff see it (M-2), and only once: a second sweep finds
# the lead already flagged.
def test_stuck_auto_reply_is_never_resent_only_flagged(session, engine, practice):
    repo = Repository(session)
    lead, reply = stuck_reply(repo, kind=Action.AUTO_REPLY)
    sender = FakeSender()
    service = make_service(engine, practice, sender)
    assert service.sweep() == SweepResult(0, 0, 0, flagged_stuck_auto_replies=1)
    assert service.sweep() == SweepResult(0, 0, 0)
    assert (repo.get_reply(reply.id).status, repo.get_reply(reply.id).send_attempts) == ("sending", 1)
    assert repo.get_lead(lead.id).needs_manual_review is True
    assert sender.calls == []


# A young auto-reply claim (within the timeout, or exactly at it) may still be in flight: no flag.
def test_young_stuck_auto_reply_is_not_flagged(session, engine, practice):
    repo = Repository(session)
    lead, _ = stuck_reply(repo, kind=Action.AUTO_REPLY, claimed_ago=timedelta(minutes=10))
    assert make_service(engine, practice, FakeSender()).sweep() == SweepResult(0, 0, 0)
    assert repo.get_lead(lead.id).needs_manual_review is False


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


# --- a failing item never stops the sweep; an acknowledgment the sweep left "failed" is retried ---

# Raises for the given reply ids (send) or lead ids (acknowledge_with_fallback), like a database
# blip or a bug in one item; every other id goes through the real method.
def raise_for(monkeypatch, *, reply_ids=(), lead_ids=()):
    original_send, original_fallback = ReplyService.send, ReplyService.acknowledge_with_fallback

    def send(self, reply_id):
        if reply_id in reply_ids:
            raise RuntimeError("database blip")
        return original_send(self, reply_id)

    def fallback(self, lead_id):
        if lead_id in lead_ids:
            raise RuntimeError("database blip")
        return original_fallback(self, lead_id)

    monkeypatch.setattr(ReplyService, "send", send)
    monkeypatch.setattr(ReplyService, "acknowledge_with_fallback", fallback)


# Final review I-1: the first item of each part raises. The sweep does not raise, still handles
# the second item of every part, flags the leads of the failed items, and counts the errors.
def test_a_raising_item_does_not_stop_the_sweep(session, engine, practice, monkeypatch):
    repo = Repository(session)
    bad_stuck_lead, bad_stuck = stuck_reply(repo, email="p1@example.com")
    _, good_stuck = stuck_reply(repo, email="p2@example.com")
    bad_stale_lead, bad_stale = drafted_first_reply(repo, email="p3@example.com")
    _, good_stale = drafted_first_reply(repo, email="p4@example.com")
    bad_lead = make_lead(repo, email="p5@example.com", age=STALE)
    good_lead = make_lead(repo, email="p6@example.com", age=STALE)
    raise_for(monkeypatch, reply_ids={bad_stuck.id, bad_stale.id}, lead_ids={bad_lead.id})
    sender = FakeSender()
    result = make_service(engine, practice, sender).sweep()
    assert result == SweepResult(1, 1, 0, 1, errors=3)
    assert sorted(c["to"] for c in sender.calls) == ["p2@example.com", "p4@example.com", "p6@example.com"]
    assert [repo.get_reply(r.id).status for r in (good_stuck, good_stale)] == ["sent", "sent"]
    for lead in (bad_stuck_lead, bad_stale_lead, bad_lead):
        assert repo.get_lead(lead.id).needs_manual_review is True
    assert repo.replies_for(good_lead.id)[0].status == "sent"


# The flag in the except is best effort: if flagging fails too, the sweep still goes on.
def test_failed_flag_after_a_raising_item_does_not_stop_the_sweep(session, engine, practice, monkeypatch):
    repo = Repository(session)
    _, bad = stuck_reply(repo, email="p1@example.com")
    _, good = stuck_reply(repo, email="p2@example.com")
    raise_for(monkeypatch, reply_ids={bad.id})

    def broken_flag(self, lead_id):
        raise RuntimeError("database down")

    monkeypatch.setattr(Repository, "flag_for_review", broken_flag)
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(1, 0, 0, errors=1)
    assert [c["to"] for c in sender.calls] == ["p2@example.com"]


# The crash window: fail_stuck_reply committed "failed" and then the re-send raised (or the process
# died). The acknowledgment is "failed" with the sweep's own error text. The next sweep claims it
# again and sends it once; a third sweep finds nothing.
def test_acknowledgment_left_failed_by_a_broken_sweep_is_retried_next_time(session, engine, practice, monkeypatch):
    repo = Repository(session)
    _, reply = stuck_reply(repo)
    raise_for(monkeypatch, reply_ids={reply.id})
    assert make_service(engine, practice, FakeSender()).sweep() == SweepResult(0, 0, 0, errors=1)
    got = repo.get_reply(reply.id)
    assert (got.status, got.error, got.send_attempts) == ("failed", STUCK_ERROR, 1)
    monkeypatch.undo()
    sender = FakeSender()
    service = make_service(engine, practice, sender)
    assert service.sweep() == SweepResult(1, 0, 0)
    assert service.sweep() == SweepResult(0, 0, 0)
    got = repo.get_reply(reply.id)
    assert (got.status, got.error, got.send_attempts) == ("sent", None, 2)
    assert [c["to"] for c in sender.calls] == [EMAIL]


# The same state, built directly: a stuck acknowledgment the sweep failed (STUCK_ERROR) is resent.
def test_failed_with_stuck_error_is_resent(session, engine, practice):
    repo = Repository(session)
    _, reply = stuck_reply(repo)
    repo.fail_stuck_reply(reply.id, claimed_before=NOW, error=STUCK_ERROR)
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(1, 0, 0)
    assert repo.get_reply(reply.id).status == "sent"
    assert len(sender.calls) == 1


# A "failed" acknowledgment with any other error (the provider refused it, known not sent) is for
# staff to resend in 4d; one at the attempt cap is left alone too, so the retry cannot loop.
def test_failed_with_other_error_or_at_cap_is_untouched(session, engine, practice):
    repo = Repository(session)
    _, refused = stuck_reply(repo, email=EMAIL)
    repo.set_reply_status(refused.id, ReplyStatus.FAILED, now=NOW - STUCK, error="SMTPRecipientsRefused")
    _, capped = stuck_reply(repo, email=OTHER, attempts=3)
    repo.fail_stuck_reply(capped.id, claimed_before=NOW, error=STUCK_ERROR)
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(0, 0, 0)
    assert sender.calls == []
    assert [(repo.get_reply(r.id).status, repo.get_reply(r.id).send_attempts) for r in (refused, capped)] == [
        ("failed", 1), ("failed", 3),
    ]


# A young claim that ended "failed" with STUCK_ERROR (claimed_at within the timeout) waits: the age
# rule is the same as for "sending" (claimed_at strictly before now - SENDING_TIMEOUT_MINUTES).
def test_failed_with_stuck_error_but_young_claim_waits(session, engine, practice):
    repo = Repository(session)
    _, reply = stuck_reply(repo, claimed_ago=timedelta(minutes=10))
    repo.fail_stuck_reply(reply.id, claimed_before=NOW, error=STUCK_ERROR)
    sender = FakeSender()
    assert make_service(engine, practice, sender).sweep() == SweepResult(0, 0, 0)
    assert sender.calls == []


# Two sweeps read the same failed-by-sweep acknowledgment before either acts; send()'s claim
# (failed -> sending, a conditional write) lets one of them send.
def test_two_overlapping_sweeps_resend_a_failed_by_sweep_acknowledgment_once(session, engine, practice, monkeypatch):
    repo = Repository(session)
    _, reply = stuck_reply(repo)
    repo.fail_stuck_reply(reply.id, claimed_before=NOW, error=STUCK_ERROR)
    read_both = threading.Barrier(2, timeout=10)
    original = Repository.failed_stuck_replies

    def read_then_wait(self, **kwargs):
        rows = original(self, **kwargs)
        read_both.wait()
        return rows

    monkeypatch.setattr(Repository, "failed_stuck_replies", read_then_wait)
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
    assert sum(r.retried_acknowledgments for r in results) == 1
    assert sum(r.errors for r in results) == 0
    assert repo.get_reply(reply.id).send_attempts == 2
