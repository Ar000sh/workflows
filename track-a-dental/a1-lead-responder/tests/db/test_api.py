"""HTTP API (A1 layer 4b, spec sections 4, 5, 6 "Intake", 7 and 8: rows 5, 9, P5, P10).

The rules under test:
- POST /leads answers 202 {"status": "received"} for a new lead, a duplicate and a suppressed
  lead alike; the pipeline runs as a background task after the answer.
- Bad input is a 4xx whose body never echoes the submitted values (they are personal data);
  an oversized body gets 413 before it is parsed or stored, declared or streamed.
- A database that cannot be reached is a generic 503, any other crash a generic 500: no traceback,
  table or column names, exception text or submitted values in the body.
- CORS lets only the configured origins in.
Real test database, FastAPI TestClient, a fake sender and a fake pipeline (no AI, no SMTP).
"""
import json
import logging
import socket
import threading
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from lead_responder import config
from lead_responder.api.app import create_app
from lead_responder.db.models import Lead, Reply
from lead_responder.db.session import make_session_factory
from lead_responder.models import Category, Classification, Risk, Urgency
from lead_responder.pipeline import GeneratedReply, PipelineResult
from lead_responder.router import Action, decide
from lead_responder.sender import Outcome, SendResult
from lead_responder.services.intake import IntakeService
from lead_responder.services.replies import ReplyService

EMAIL = "anna.patient@example.com"
MESSAGE = "Ich moechte einen Termin fuer eine Kontrolle."
GOOD = {"name": "Anna Test", "email": EMAIL, "phone": None, "message": MESSAGE, "consent": True}
LIMIT = 2000


def utcnow() -> datetime:
    return datetime.now(UTC)


# Records every send (thread-safe) and accepts it.
class FakeSender:
    def __init__(self):
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def send(self, *, to: str, subject: str, body: str) -> SendResult:
        with self._lock:
            self.calls.append({"to": to, "subject": subject, "body": body})
        return SendResult(Outcome.ACCEPTED, "")


# Stands in for process_inquiry: the classifier "failed", so the lead gets the acknowledgment.
def fake_pipeline(**_kwargs) -> PipelineResult:
    return PipelineResult(
        None, decide(None), (GeneratedReply(Action.ACKNOWLEDGMENT, "Hallo / Hello", None),), (), 0.0,
    )


class Env:
    def __init__(self, intake, sender, client):
        self.intake, self.sender, self.client = intake, sender, client


# Builds the app around a real IntakeService on the test database. Services are shut down at the
# end so no background run outlives the test that started it.
@pytest.fixture
def make_env(engine, session, practice):
    built: list[IntakeService] = []

    def build(*, session_factory=None, max_body_bytes=LIMIT, cors_origins=None, intake_wrapper=None):
        sender = FakeSender()
        factory = session_factory or make_session_factory(engine)
        replies = ReplyService(session_factory=factory, sender=sender, practice=practice, clock=utcnow)
        intake = IntakeService(
            session_factory=factory, replies=replies, llm=object(), practice=practice, faq="FAQ",
            clock=utcnow, pipeline=fake_pipeline,
        )
        built.append(intake)
        app = create_app(
            intake=intake_wrapper(intake) if intake_wrapper else intake,
            max_body_bytes=max_body_bytes, cors_origins=cors_origins,
        )
        return Env(intake, sender, TestClient(app, raise_server_exceptions=False))

    yield build
    for intake in built:
        intake.shutdown()


def lead_count(session: Session) -> int:
    session.expire_all()
    return session.scalar(select(func.count()).select_from(Lead))


def test_health_returns_ok(make_env):
    env = make_env()
    response = env.client.get("/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


def test_post_valid_lead_returns_202_and_stores_it(session, make_env):
    env = make_env()
    response = env.client.post("/leads", json=GOOD)
    assert (response.status_code, response.json()) == (202, {"status": "received"})
    session.expire_all()
    [lead] = session.scalars(select(Lead)).all()
    assert (lead.name, lead.email, lead.message) == ("Anna Test", EMAIL, MESSAGE)
    # The background task ran after the answer: the acknowledgment went out through the sender.
    [call] = env.sender.calls
    assert call["to"] == EMAIL
    assert session.scalar(select(Reply.status)) == "sent"


def test_duplicate_post_returns_same_answer_and_stores_one_lead(session, make_env):
    env = make_env()
    first = env.client.post("/leads", json=GOOD)
    second = env.client.post("/leads", json=GOOD)
    assert (second.status_code, second.json()) == (first.status_code, first.json()) == (202, {"status": "received"})
    assert lead_count(session) == 1
    assert len(env.sender.calls) == 1


def test_response_does_not_reveal_whether_a_lead_was_a_duplicate_or_limited(session, make_env, monkeypatch):
    monkeypatch.setattr(config, "MAX_LEADS_PER_EMAIL_HOUR", 1)
    env = make_env()
    new = env.client.post("/leads", json=GOOD)
    duplicate = env.client.post("/leads", json=GOOD)
    limited = env.client.post("/leads", json={**GOOD, "message": "Eine ganz andere Frage zur Praxis."})
    assert lead_count(session) == 2  # the duplicate was dropped, the limited lead was saved
    assert [r.status_code for r in (new, duplicate, limited)] == [202, 202, 202]
    assert new.content == duplicate.content == limited.content
    assert len(env.sender.calls) == 1  # nothing was sent for the suppressed lead


def test_missing_consent_returns_422_and_stores_nothing(session, make_env):
    env = make_env()
    for body in ({**GOOD, "consent": False}, {k: v for k, v in GOOD.items() if k != "consent"}):
        assert env.client.post("/leads", json=body).status_code == 422
    assert lead_count(session) == 0
    assert env.sender.calls == []


def test_newline_in_email_returns_422(session, make_env):
    env = make_env()
    response = env.client.post("/leads", json={**GOOD, "email": "a@example.com\nBcc: x@example.com"})
    assert response.status_code == 422
    assert lead_count(session) == 0


def test_validation_error_body_does_not_contain_the_submitted_values(make_env):
    env = make_env(max_body_bytes=50_000)
    secret_email = "not-an-email-secret"
    secret_message = "private medical details " + "x" * 10_001
    response = env.client.post(
        "/leads", content=_json({**GOOD, "email": secret_email, "message": secret_message}),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert secret_email not in response.text and "private medical" not in response.text
    fields = {item["field"] for item in response.json()["detail"]}
    assert {"email", "message"} <= fields


def _json(payload: dict) -> bytes:
    return json.dumps(payload).encode()


def test_body_over_limit_returns_413_before_parsing_and_stores_nothing(session, make_env):
    env = make_env()
    big = _json({**GOOD, "message": "a" * (LIMIT + 1)})
    headers = {"content-type": "application/json"}
    # 1. an honest Content-Length over the limit
    declared = env.client.post("/leads", content=big, headers=headers)
    # 2. a chunked body (no Content-Length) that grows past the limit
    chunked = env.client.post("/leads", content=(big[i:i + 500] for i in range(0, len(big), 500)), headers=headers)
    # 3. a Content-Length that lies low while the body is big
    lying = env.client.post("/leads", content=big, headers={**headers, "content-length": "10"})
    for response in (declared, chunked, lying):
        assert (response.status_code, response.json()) == (413, {"detail": "request too large"})
    assert lead_count(session) == 0
    assert env.sender.calls == []


def test_non_json_body_returns_4xx(make_env):
    env = make_env()
    for content, ctype in ((b"name=Anna", "application/x-www-form-urlencoded"), (b"{not json", "application/json"),
                           (b"[1, 2]", "application/json"), (b"", "application/json")):
        response = env.client.post("/leads", content=content, headers={"content-type": ctype})
        assert 400 <= response.status_code < 500, (content, response.status_code)


# A port nothing listens on: bind to a free one, then let it go.
def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_database_down_returns_503_with_generic_text_and_sends_nothing(make_env, caplog):
    dead = make_session_factory(create_engine(
        f"postgresql+psycopg://leads:leads@127.0.0.1:{closed_port()}/leads", connect_args={"connect_timeout": 2},
    ))
    env = make_env(session_factory=dead)
    with caplog.at_level(logging.DEBUG):
        response = env.client.post("/leads", json=GOOD)
    assert response.status_code == 503
    assert response.json() == {"detail": "service temporarily unavailable"}
    assert EMAIL not in response.text and "Traceback" not in response.text
    assert env.sender.calls == []
    assert EMAIL not in caplog.text and MESSAGE not in caplog.text


# Wraps the real service but crashes in receive() with a message full of internals and the
# submitted email, the way a driver error text could.
class ExplodingIntake:
    def __init__(self, inner):
        self._inner = inner

    def receive(self, lead):
        raise RuntimeError(f'null value in column "message" of relation "leads" for {lead.email}')

    def process(self, lead_id):
        self._inner.process(lead_id)


def test_unexpected_error_returns_500_without_internals(make_env, caplog):
    env = make_env(intake_wrapper=ExplodingIntake)
    with caplog.at_level(logging.DEBUG):
        response = env.client.post("/leads", json=GOOD)
    assert response.status_code == 500
    assert response.json() == {"detail": "internal error"}
    for leaked in ("Traceback", "leads", "column", "relation", EMAIL, "RuntimeError"):
        assert leaked not in response.text
    # The log keeps the class name and nothing from the request.
    assert "RuntimeError" in caplog.text
    assert EMAIL not in caplog.text and "column" not in caplog.text


def test_cors_only_allows_configured_origins(make_env):
    env = make_env(cors_origins=["https://brightsmile.example"])
    allowed = env.client.post("/leads", json=GOOD, headers={"origin": "https://brightsmile.example"})
    other = env.client.post("/leads", json=GOOD, headers={"origin": "https://evil.example"})
    assert allowed.headers["access-control-allow-origin"] == "https://brightsmile.example"
    assert "access-control-allow-origin" not in other.headers
    preflight = env.client.options("/leads", headers={
        "origin": "https://brightsmile.example", "access-control-request-method": "DELETE",
    })
    assert preflight.status_code == 400  # only POST and GET are allowed

    default = make_env()
    none = default.client.post("/leads", json=GOOD, headers={"origin": "https://brightsmile.example"})
    assert "access-control-allow-origin" not in none.headers


def test_importing_api_main_connects_to_nothing():
    import importlib
    main = importlib.import_module("lead_responder.api.main")
    assert callable(main.app) and callable(main.build_app)
