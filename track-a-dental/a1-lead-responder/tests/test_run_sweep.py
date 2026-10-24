"""scripts/run_sweep.main(): prints every count and exits 1 when an item raised, so a scheduler alerts.

No database and no SMTP: ReplyService is replaced by a fake whose sweep() returns a fixed result
(make_engine only builds a connection pool, it does not connect).
"""
import pytest

from lead_responder.scripts import run_sweep
from lead_responder.services.replies import SweepResult


def run_with(monkeypatch, result: SweepResult) -> int:
    class FakeService:
        def __init__(self, **kwargs):
            pass

        def sweep(self):
            return result

    monkeypatch.setattr(run_sweep, "ReplyService", FakeService)
    return run_sweep.main()


def test_exit_code_0_without_errors(monkeypatch, capsys):
    assert run_with(monkeypatch, SweepResult(1, 2, 3, 4)) == 0
    out = capsys.readouterr().out
    assert "0 error(s)" in out


@pytest.mark.parametrize("errors", [1, 5])
def test_exit_code_1_with_errors_and_all_counts_printed(monkeypatch, capsys, errors):
    result = SweepResult(1, 2, 3, 4, errors=errors, flagged_stuck_auto_replies=6)
    assert run_with(monkeypatch, result) == 1
    out = capsys.readouterr().out
    assert "Retried 1 " in out and "sent 4 " in out and "sent 2 " in out and "left 3 " in out
    assert "flagged 6 " in out
    assert f"{errors} error(s)" in out
