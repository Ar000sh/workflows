"""Read queries for the 4b limits and sweep: recent-lead counts, stuck sends, leads with no first reply."""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from lead_responder.db.models import LeadStatus, ReplyStatus
from lead_responder.limits import mailbox_key
from lead_responder.repository import Repository
from lead_responder.router import Action

NOW = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)
HOUR_AGO = NOW - timedelta(hours=1)


def make_lead(repo: Repository, email="anna@example.com", message="Ich habe Zahnschmerzen.", now=NOW):
    lead, _ = repo.create_lead_if_new(
        name="Anna Test", email=email, phone=None, message=message,
        consent_at=now, now=now, mailbox_key=mailbox_key(email),
    )
    return lead


# --- counts for the per-address and global limits ---

# The limit is checked AFTER the lead is saved, so the new lead counts itself ("including the new
# lead itself", spec section 6). Aliases of one mailbox count together, other mailboxes do not.
def test_count_includes_the_new_lead_itself(session):
    repo = Repository(session)
    make_lead(repo, email="Victim+1@Example.com")
    assert repo.count_leads_for_mailbox_since("victim@example.com", HOUR_AGO) == 1
    make_lead(repo, email="victim+2@example.com", message="anders")
    make_lead(repo, email="someone@example.com")
    assert repo.count_leads_for_mailbox_since("victim@example.com", HOUR_AGO) == 2
    assert repo.count_leads_since(HOUR_AGO) == 3
    assert not session.in_transaction()


# Two forms for one mailbox at the same moment: each saves its lead, then counts. Both must see
# both leads (2), so a flood cannot slip under the limit by arriving in parallel.
def test_two_simultaneous_leads_for_one_mailbox_both_count_two(engine, session):
    barrier_save = threading.Barrier(2)
    barrier_count = threading.Barrier(2)

    def submit(i):
        with Session(engine, expire_on_commit=False) as s:
            repo = Repository(s)
            barrier_save.wait(timeout=5)
            make_lead(repo, email=f"victim+{i}@example.com", message=f"Nachricht {i}")
            barrier_count.wait(timeout=5)
            return repo.count_leads_for_mailbox_since("victim@example.com", HOUR_AGO)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(submit, range(2))) == [2, 2]


# The window is "created_at >= since": a lead exactly at the boundary is counted, one a
# microsecond older is not. (Same choice for both counts.)
def test_counts_respect_since_boundary(session):
    repo = Repository(session)
    make_lead(repo, email="a@example.com", now=HOUR_AGO)
    make_lead(repo, email="a@example.com", message="older", now=HOUR_AGO - timedelta(microseconds=1))
    assert repo.count_leads_for_mailbox_since("a@example.com", HOUR_AGO) == 1
    assert repo.count_leads_since(HOUR_AGO) == 1
    assert repo.count_leads_since(HOUR_AGO - timedelta(microseconds=1)) == 2


# --- sweep part 1: sends stuck in "sending" ---

def _reply(repo, lead_id, kind, *, claims=0, claimed_at=NOW):
    reply = repo.add_reply(lead_id, kind=kind, ai_body=None, body="x", status=ReplyStatus.DRAFTED, now=NOW)
    for n in range(claims):
        if n:
            repo.set_reply_status(reply.id, ReplyStatus.FAILED, now=claimed_at, error="SMTPException")
        repo.set_reply_status(reply.id, ReplyStatus.SENDING, now=claimed_at)
    return reply


def test_stuck_sending_replies_filters_kind_age_and_attempts(session):
    repo = Repository(session)
    old, young = NOW - timedelta(minutes=11), NOW - timedelta(minutes=9)
    cutoff = NOW - timedelta(minutes=10)
    lead = make_lead(repo)
    stuck = _reply(repo, lead.id, Action.ACKNOWLEDGMENT, claims=1, claimed_at=old)
    stuck_twice = _reply(repo, lead.id, Action.ACKNOWLEDGMENT, claims=2, claimed_at=old)
    _reply(repo, lead.id, Action.ACKNOWLEDGMENT, claims=3, claimed_at=old)  # at the attempt cap
    _reply(repo, lead.id, Action.ACKNOWLEDGMENT, claims=1, claimed_at=young)  # still within the timeout
    _reply(repo, lead.id, Action.ACKNOWLEDGMENT, claims=1, claimed_at=cutoff)  # exactly at it: strictly older only
    _reply(repo, lead.id, Action.DRAFT, claims=1, claimed_at=old)  # drafts are never retried automatically
    _reply(repo, lead.id, Action.ACKNOWLEDGMENT)  # drafted, never claimed
    sent = _reply(repo, lead.id, Action.ACKNOWLEDGMENT, claims=1, claimed_at=old)
    repo.set_reply_status(sent.id, ReplyStatus.SENT, now=old)

    found = repo.stuck_sending_replies(kind=Action.ACKNOWLEDGMENT, claimed_before=cutoff, max_attempts=3)
    assert [r.id for r in found] == [stuck.id, stuck_twice.id]
    drafts = repo.stuck_sending_replies(kind=Action.DRAFT, claimed_before=cutoff, max_attempts=3)
    assert len(drafts) == 1 and drafts[0].kind == "draft"
    assert not session.in_transaction()


# --- sweep part 2: leads that never got a first reply ---

def test_leads_without_first_reply_skips_suppressed_replied_and_young_leads(session):
    repo = Repository(session)
    old = NOW - timedelta(minutes=6)
    cutoff = NOW - timedelta(minutes=5)

    def lead(name, now=old):
        return make_lead(repo, email=f"{name}@example.com", now=now)

    lost_new = lead("new")
    lost_classified = lead("classified")
    repo.set_lead_status(lost_classified.id, LeadStatus.CLASSIFIED)
    lost_acknowledged = lead("acknowledged")
    repo.set_lead_status(lost_acknowledged.id, LeadStatus.ACKNOWLEDGED)
    with_draft_only = lead("draftonly")  # a staff draft is not a first reply
    repo.add_reply(with_draft_only.id, kind=Action.DRAFT, ai_body=None, body="x", status=ReplyStatus.DRAFTED, now=old)

    lead("young", now=NOW - timedelta(minutes=4))
    lead("edge", now=cutoff)  # exactly at the cutoff: strictly older only
    repo.suppress_lead(lead("suppressed").id)
    repo.acknowledge_lead(lead("acked").id, kind=Action.ACKNOWLEDGMENT, ai_body=None, body="x", now=old)
    auto = lead("auto")
    repo.set_lead_status(auto.id, LeadStatus.CLASSIFIED)
    repo.acknowledge_lead(auto.id, kind=Action.AUTO_REPLY, ai_body="x", body="x", now=old)
    done = lead("done")
    for status in (LeadStatus.ACKNOWLEDGED, LeadStatus.DONE):
        repo.set_lead_status(done.id, status)
    anonymized = make_lead(repo, email="anon@example.com", now=NOW - timedelta(days=100))
    repo.anonymize_older_than(cutoff=NOW - timedelta(days=90), now=NOW)

    found = repo.leads_without_first_reply(created_before=cutoff)
    assert not session.in_transaction()
    assert [ld.id for ld in found] == [
        lost_new.id, lost_classified.id, lost_acknowledged.id, with_draft_only.id,
    ]
    assert anonymized.id not in [ld.id for ld in found]
