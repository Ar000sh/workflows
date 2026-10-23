"""DSGVO retention: the one rule "personal data lives RETENTION_DAYS, then it is blanked"."""
from __future__ import annotations

from datetime import datetime, timedelta

from lead_responder.repository import Repository


# Turns "keep for N days" into a cutoff timestamp and asks the repository to anonymize
# everything older. `now` is passed in (not read here) so tests can pin the clock.
# days <= 0 would wipe fresh leads, so it is rejected instead of trusted.
def run(repo: Repository, *, days: int, now: datetime) -> int:
    if days <= 0:
        raise ValueError(f"days must be positive, got {days}")
    return repo.anonymize_older_than(cutoff=now - timedelta(days=days), now=now)
