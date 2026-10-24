"""Entry point for uvicorn:  uv run uvicorn lead_responder.api.main:app

`app` is a thin ASGI wrapper that builds the real application on its first call (the server's
start-up message), not at import. Importing this module therefore needs no database, no AI key
and no SMTP server, which keeps tests and tools light. `uvicorn --factory
lead_responder.api.main:build_app` works too.
"""
from dotenv import load_dotenv

load_dotenv()  # before importing config, so .env overrides apply

import threading  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from starlette.types import Receive, Scope, Send  # noqa: E402

from lead_responder import config  # noqa: E402
from lead_responder.api.app import create_app  # noqa: E402
from lead_responder.db.session import make_engine, make_session_factory  # noqa: E402
from lead_responder.drafter import load_faq  # noqa: E402
from lead_responder.practice import load_practice  # noqa: E402
from lead_responder.sender import SmtpSender  # noqa: E402
from lead_responder.services.intake import IntakeService  # noqa: E402
from lead_responder.services.replies import ReplyService  # noqa: E402
from shared.llm import LLMClient  # noqa: E402


# Wires the real pieces from config: the same Postgres, SMTP sender and practice as
# scripts/run_sweep.py, plus the AI client and the FAQ for the pipeline. MAIL_FROM empty means
# the practice sends as itself; replies from patients go to the practice address either way.
def build_app() -> FastAPI:
    practice = load_practice()
    sender = SmtpSender(
        host=config.SMTP_HOST, port=config.SMTP_PORT, from_addr=config.MAIL_FROM or practice.email,
        reply_to=practice.email, starttls=config.SMTP_STARTTLS, user=config.SMTP_USER,
        password=config.SMTP_PASSWORD, timeout=config.SMTP_TIMEOUT_SECONDS,
    )
    session_factory = make_session_factory(make_engine(config.DATABASE_URL))
    clock = lambda: datetime.now(timezone.utc)  # noqa: E731
    replies = ReplyService(session_factory=session_factory, sender=sender, practice=practice, clock=clock)
    intake = IntakeService(
        session_factory=session_factory, replies=replies, llm=LLMClient(), practice=practice,
        faq=load_faq(), clock=clock,
    )
    return create_app(intake=intake, max_body_bytes=config.MAX_BODY_BYTES, cors_origins=config.CORS_ORIGINS)


# Builds the application once, on the first ASGI call, and hands every call to it.
class _LazyApp:
    def __init__(self) -> None:
        self._app: FastAPI | None = None
        self._lock = threading.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._app is None:
            with self._lock:
                if self._app is None:
                    self._app = build_app()
        await self._app(scope, receive, send)


app = _LazyApp()
