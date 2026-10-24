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


# Nothing moves to its own status except a failed reply (a failed resend writes the new error).
def test_only_failed_replies_may_repeat_their_status():
    for status, targets in REPLY_TRANSITIONS.items():
        assert (status in targets) == (status is ReplyStatus.FAILED)
    for status, targets in LEAD_TRANSITIONS.items():
        assert status not in targets
