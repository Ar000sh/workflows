"""The HTTP API: POST /leads (the website form) and GET /health.

Request path, outside in:
  CORS            only the configured origins get the allow-origin header
  _Guard          body-size limit (413) and generic error answers (503, 500)
  FastAPI         parses and validates the JSON into LeadIn (the only place form input is checked)
  post_lead       intake.receive(lead) while the form waits; the AI work runs after the answer

The API answers a lead the same way whatever happened to it (new, duplicate, suppressed by the
per-email limit): an attacker must not learn which addresses exist or when a limit is reached.
Every error body is generic, because the input is personal data and exception text can quote it.
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from lead_responder import config
from lead_responder.api.schemas import LeadIn
from lead_responder.services.intake import IntakeService

log = logging.getLogger(__name__)


# Raised from inside the body read. It derives from BaseException on purpose: FastAPI turns every
# Exception raised while reading the body into a 400 "error parsing the body", which would hide
# the 413. Only _Guard catches it.
class _BodyTooLarge(BaseException):
    pass


# Pure ASGI middleware (not BaseHTTPMiddleware, which would read the body before we can count it).
#  - 413: Content-Length above the limit is refused at once; a body without Content-Length
#    (chunked) or with a false one is counted while the app reads it, and the read raises
#    _BodyTooLarge as soon as the limit is passed. Nothing is parsed or stored by then.
#  - 503: the database cannot be reached (OperationalError / InterfaceError).
#  - 500: any other exception. Both bodies are fixed text, and the log gets the exception class
#    name only (its message could quote the email or the message text).
# A response that has already started cannot be replaced, so then the error is only logged.
class _Guard:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_body_bytes:
            await self._answer(413, "request too large", tracking_send)
            return

        seen = 0

        async def counting_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_body_bytes:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await self._answer(413, "request too large", tracking_send)
        except (OperationalError, InterfaceError) as e:
            log.error("database unavailable (%s)", type(e).__name__)
            if not started:
                await self._answer(503, "service temporarily unavailable", tracking_send)
        except Exception as e:
            log.error("request failed (%s)", type(e).__name__)
            if not started:
                await self._answer(500, "internal error", tracking_send)

    @staticmethod
    async def _answer(status: int, detail: str, send: Send) -> None:
        body = json.dumps({"detail": detail}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


# Builds the app around one IntakeService (made once at start-up by api/main.py, or by a test).
# cors_origins=None means no browser origin is allowed; only POST and GET are ever allowed.
def create_app(
    *, intake: IntakeService, max_body_bytes: int = config.MAX_BODY_BYTES,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    # On shutdown wait for running pipelines, so a stop does not cut an acknowledgment in half.
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        intake.shutdown()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    # Added first = inner, so the guard's 413/503/500 answers still pass through CORS.
    app.add_middleware(_Guard, max_body_bytes=max_body_bytes)
    app.add_middleware(
        CORSMiddleware, allow_origins=cors_origins or [], allow_methods=["POST", "GET"],
        allow_headers=["content-type"],
    )

    # FastAPI's default 422 body echoes the submitted values ("input"), which are personal data.
    # Only the field name and a fixed text go back.
    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = []
        for error in exc.errors():
            location = [str(part) for part in error["loc"] if part != "body"]
            fields.append({"field": ".".join(location) or "body", "message": "invalid value"})
        return JSONResponse({"detail": fields}, status_code=422)

    # A plain def, so FastAPI runs it in a worker thread: receive() does blocking database work.
    # Always 202 {"status": "received"}: None (duplicate) and run_pipeline=False (suppressed)
    # look the same as a new lead from outside.
    @app.post("/leads", status_code=202)
    def post_lead(lead: LeadIn, background_tasks: BackgroundTasks) -> dict[str, str]:
        received = intake.receive(lead)
        if received is not None and received.run_pipeline:
            background_tasks.add_task(intake.process, received.lead_id)
        return {"status": "received"}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
