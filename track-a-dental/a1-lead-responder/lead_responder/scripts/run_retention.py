"""Daily DSGVO job: blank personal data of leads older than RETENTION_DAYS and print the count.

Run:  uv run python -m lead_responder.scripts.run_retention
Safe to run any number of times (already anonymized leads are skipped).
"""
from dotenv import load_dotenv

load_dotenv()  # before importing config, so .env overrides apply

from datetime import datetime, timezone  # noqa: E402

from sqlalchemy.orm import Session  # noqa: E402

from lead_responder import config, retention  # noqa: E402
from lead_responder.db.session import make_engine  # noqa: E402
from lead_responder.repository import Repository  # noqa: E402


def main() -> int:
    engine = make_engine(config.DATABASE_URL)
    with Session(engine) as session:
        count = retention.run(
            Repository(session), days=config.RETENTION_DAYS, now=datetime.now(timezone.utc),
        )
    print(f"Anonymized {count} lead(s) older than {config.RETENTION_DAYS} days.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
