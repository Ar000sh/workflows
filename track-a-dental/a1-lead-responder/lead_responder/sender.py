"""Sending one email, with a typed result instead of exceptions.

Why typed: the caller (ReplyService) must record three different facts, and an exception cannot
say which one happened:
  ACCEPTED  the provider took the mail          -> reply "sent"
  FAILED    known NOT sent (refused, bad input) -> reply "failed", staff can resend
  TIMEOUT   unknown: it may have gone out       -> reply stays "sending", nobody resends blindly
The retry policy lives here (A1 spec section 7): a refused or unreachable provider is tried up to
3 more times, waiting 1, 2 and 4 seconds; a timeout is never retried, because the first attempt
may already have been delivered and a retry would send the patient a second copy.
"""
from __future__ import annotations

import smtplib
import time
from dataclasses import dataclass
from email.message import EmailMessage
from enum import StrEnum
from typing import Callable, Protocol

# Waits before retry 1, 2 and 3. Four attempts in total.
RETRY_WAITS = (1, 2, 4)


class Outcome(StrEnum):
    ACCEPTED = "accepted"
    FAILED = "failed"
    TIMEOUT = "timeout"


# detail is the exception class name (or "" on success). Never the exception text: providers
# repeat the recipient address in their errors, and detail is stored and may be logged.
@dataclass(frozen=True)
class SendResult:
    outcome: Outcome
    detail: str = ""


# What ReplyService needs from any sender: SMTP today, an n8n webhook or a test fake later.
class Sender(Protocol):
    def send(self, *, to: str, subject: str, body: str) -> SendResult: ...


class SmtpSender:
    # Settings come from config.py (SMTP_*) and the practice; `sleep` is injected so tests can
    # record the retry waits instead of really waiting 7 seconds.
    def __init__(
        self, *, host: str, port: int, from_addr: str, reply_to: str, starttls: bool = False,
        user: str | None = None, password: str | None = None, timeout: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.host, self.port, self.timeout = host, port, timeout
        self.from_addr, self.reply_to = from_addr, reply_to
        self.starttls, self.user, self.password = starttls, user, password
        self._sleep = sleep

    # Builds the plain-text message once, then tries to hand it over: one fresh SMTP connection per
    # attempt (a broken connection is never reused), each with the socket timeout.
    # A CR or LF in the recipient or subject would let the text after it become a new header
    # ("Bcc: ..."): refused before any connection is opened. Intake's email validation already
    # forbids newlines; this is the last line of defence for any other caller.
    def send(self, *, to: str, subject: str, body: str) -> SendResult:
        if any(char in value for value in (to, subject) for char in "\r\n"):
            return SendResult(Outcome.FAILED, "HeaderInjectionRefused")
        msg = EmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = to
        msg["Reply-To"] = self.reply_to
        msg["Subject"] = subject
        msg.set_content(body)  # text/plain; utf-8 is chosen automatically for non-ASCII text

        detail = ""
        for wait in (*RETRY_WAITS, None):
            try:
                self._deliver(msg)
                return SendResult(Outcome.ACCEPTED)
            except TimeoutError as e:  # socket.timeout is the same class since Python 3.10
                return SendResult(Outcome.TIMEOUT, type(e).__name__)
            except (OSError, smtplib.SMTPException) as e:  # refused, unreachable, auth, 4xx/5xx
                detail = type(e).__name__
            if wait is None:
                break
            self._sleep(wait)
        return SendResult(Outcome.FAILED, detail)

    # One attempt: connect, optionally upgrade to TLS and log in, send. The `with` block sends QUIT
    # and closes the socket whatever happens.
    def _deliver(self, msg: EmailMessage) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            if self.starttls:
                smtp.starttls()
            if self.user:
                smtp.login(self.user, self.password or "")
            smtp.send_message(msg)
