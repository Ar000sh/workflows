"""The transition tables are plain data: check them without a database."""
from lead_responder.db.models import LEAD_TRANSITIONS, REPLY_TRANSITIONS, LeadStatus, ReplyStatus


# Every status has an entry (a forgotten one would silently make it a dead end), and every target
# is a real status of the same enum.
def test_every_status_has_an_entry_and_only_real_targets():
    for table, enum in ((LEAD_TRANSITIONS, LeadStatus), (REPLY_TRANSITIONS, ReplyStatus)):
        assert set(table) == set(enum)
        for targets in table.values():
            assert targets <= set(enum)


# Final means no way out: this is where "sent is final" is written down.
def test_final_statuses_have_no_exit():
    assert REPLY_TRANSITIONS[ReplyStatus.SENT] == frozenset()
    assert REPLY_TRANSITIONS[ReplyStatus.REJECTED] == frozenset()
    assert LEAD_TRANSITIONS[LeadStatus.DONE] == frozenset()


# The reply table written out pair by pair (6 x 6): the claim "-> sending" is the only way to a send,
# and a failed reply can only be claimed again, never marked sent or failed directly.
def test_reply_transitions_are_exactly_these():
    S = ReplyStatus
    allowed = {
        (S.DRAFTED, S.APPROVED), (S.DRAFTED, S.REJECTED), (S.DRAFTED, S.SENDING), (S.DRAFTED, S.FAILED),
        (S.APPROVED, S.SENDING), (S.APPROVED, S.FAILED),
        (S.SENDING, S.SENT), (S.SENDING, S.FAILED),
        (S.FAILED, S.SENDING),
    }
    for old in S:
        for new in S:
            assert (new in REPLY_TRANSITIONS[old]) == ((old, new) in allowed), (old, new)


# Nothing moves to its own status, for leads or replies (a failed resend goes through "sending").
def test_no_status_repeats_itself():
    for table in (REPLY_TRANSITIONS, LEAD_TRANSITIONS):
        for status, targets in table.items():
            assert status not in targets
