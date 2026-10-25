"""Sending one email, with a typed result instead of exceptions.

Why typed: the caller (ReplyService) must record three different facts, and an exception cannot
say which one happened:
  ACCEPTED  the provider took the mail          -> reply "sent"
  FAILED    known NOT sent (refused, bad input) -> reply "failed", staff can resend
  TIMEOUT   unknown: it may have gone out       -> reply stays "sending", nobody resends blindly
The retry policy lives here (A1 spec section 7): a refused or unreachable provider (or a server
that answers with a refusal) is tried up to 3 more times, waiting 1, 2 and 4 seconds. A timeout,
or a connection lost while the message was being handed over, is never retried: the first attempt
may already have been delivered and a retry would send the patient a second copy (see _attempt).
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
    # record the retry waits instead of really waiting 7 seconds. local_hostname is our EHLO name:
    # given, so smtplib does not look up this machine's DNS name (socket.getfqdn, ~1 s) per connection.
    def __init__(
        self, *, host: str, port: int, from_addr: str, reply_to: str, starttls: bool = False,
        user: str | None = None, password: str | None = None, timeout: float = 10.0,
        local_hostname: str = "localhost", sleep: Callable[[float], None] = time.sleep,
    ):
        self.host, self.port, self.timeout = host, port, timeout
        self.local_hostname = local_hostname
        self.from_addr, self.reply_to = from_addr, reply_to
        self.starttls, self.user, self.password = starttls, user, password
        self._sleep = sleep

    # Builds the plain-text message once, then tries to hand it over: one fresh SMTP connection per
    # attempt (a broken connection is never reused), each with the socket timeout.
    # A CR or LF in the recipient or subject would let the text after it become a new header
    # ("Bcc: ..."): refused before any connection is opened. Intake's email validation already
    # forbids newlines; this is the last line of defence for any other caller. Its detail
    # "HeaderInjectionRefused" is a fixed marker, not an exception class name (nothing was raised).
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
            result = self._attempt(msg)
            if result.outcome is not Outcome.FAILED:
                return result
            detail = result.detail
            if wait is None:
                break
            self._sleep(wait)
        return SendResult(Outcome.FAILED, detail)

    # One attempt on a fresh connection; the `with` block sends QUIT and closes the socket.
    # An error is judged by the STAGE it happened in, because that decides whether the patient
    # may already have the email:
    #   setup (connect, EHLO, STARTTLS, login): nothing of the message has left yet, so a refused
    #       or dropped connection or an auth error is FAILED, and send() retries it;
    #   send (send_message): the server answering with a refusal (4xx/5xx, recipients or sender
    #       refused) means known not accepted: FAILED, retried. A dropped connection or any other
    #       OSError may have happened after the body arrived: TIMEOUT (unknown), never retried.
    #       Real smtplib turns a read timeout into SMTPServerDisconnected, so this matters;
    #   done (send_message returned): the server accepted the mail; a bad QUIT changes nothing.
    # A timeout at any stage is TIMEOUT, also when smtplib wrapped it (see _caused_by_timeout).
    def _attempt(self, msg: EmailMessage) -> SendResult:
        stage = "setup"
        try:
            with smtplib.SMTP(
                self.host, self.port, local_hostname=self.local_hostname, timeout=self.timeout,
            ) as smtp:
                if self.starttls:
                    smtp.starttls()
                if self.user:
                    smtp.login(self.user, self.password or "")
                stage = "send"
                smtp.send_message(msg)
                stage = "done"
            return SendResult(Outcome.ACCEPTED)
        except (OSError, smtplib.SMTPException) as e:  # SMTPException is an OSError; listed for clarity
            name = type(e).__name__
            if stage == "done":
                return SendResult(Outcome.ACCEPTED)
            if _caused_by_timeout(e):
                return SendResult(Outcome.TIMEOUT, name)
            refused = isinstance(e, (smtplib.SMTPResponseException, smtplib.SMTPRecipientsRefused))
            if stage == "send" and not refused:
                return SendResult(Outcome.TIMEOUT, name)
            return SendResult(Outcome.FAILED, name)


# True if the error is a timeout or was raised while handling one (smtplib re-raises a socket
# timeout as SMTPServerDisconnected inside its `except OSError`, which sets __context__).
def _caused_by_timeout(error: BaseException | None) -> bool:
    seen = 0
    while error is not None and seen < 10:  # the cap guards against a cyclic chain
        if isinstance(error, TimeoutError):  # socket.timeout is the same class since Python 3.10
            return True
        error = error.__cause__ or error.__context__
        seen += 1
    return False
