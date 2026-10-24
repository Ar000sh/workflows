"""Scheduled job: resend stuck acknowledgments and acknowledge leads that got no reply.

Run:  uv run python -m lead_responder.scripts.run_sweep
Meant to run every few minutes from a scheduler (chosen with the deployment layer). Safe to run
any number of times, also two at once: each stuck reply and each lead gets at most one email.
"""
from dotenv import load_dotenv

load_dotenv()  # before importing config, so .env overrides apply

from datetime import datetime, timezone  # noqa: E402

from lead_responder import config  # noqa: E402
from lead_responder.db.session import make_engine, make_session_factory  # noqa: E402
from lead_responder.practice import load_practice  # noqa: E402
from lead_responder.sender import SmtpSender  # noqa: E402
from lead_responder.services.replies import ReplyService  # noqa: E402


# Wires the real pieces (Postgres, SMTP from config, the practice file) into one ReplyService,
# runs one sweep and prints its three counts. MAIL_FROM empty means the practice sends as itself;
# replies from patients go to the practice address either way.
def main() -> int:
    practice = load_practice()
    sender = SmtpSender(
        host=config.SMTP_HOST, port=config.SMTP_PORT, from_addr=config.MAIL_FROM or practice.email,
        reply_to=practice.email, starttls=config.SMTP_STARTTLS, user=config.SMTP_USER,
        password=config.SMTP_PASSWORD, timeout=config.SMTP_TIMEOUT_SECONDS,
    )
    service = ReplyService(
        session_factory=make_session_factory(make_engine(config.DATABASE_URL)), sender=sender,
        practice=practice, clock=lambda: datetime.now(timezone.utc),
    )
    result = service.sweep()
    print(
        f"Retried {result.retried_acknowledgments} stuck acknowledgment(s), sent "
        f"{result.fallback_acknowledgments} fallback acknowledgment(s), left "
        f"{result.left_for_staff} for staff."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
