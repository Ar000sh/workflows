"""SmtpSender: typed results, the retry policy (A1 spec section 7) and header-injection refusal.

No real mail is sent: smtplib.SMTP is replaced by a fake that follows a script, one entry per
connection attempt (None = this attempt works, an exception = this attempt raises it).
"""
import smtplib

import pytest

from lead_responder import sender as sender_module
from lead_responder.sender import Outcome, SmtpSender

TO = "anna.patient@example.com"
SUBJECT = "Ihre Anfrage an die Zahnarztpraxis BrightSmile"
BODY = "Hallo Anna,\n\nvielen Dank. Grüße aus der Zahnärztlichen Praxis."


# Builds a fake SMTP class for one test. `script` lists what each attempt does; `stage` says
# where the exception is raised: "connect" (the constructor) or "send" (send_message).
def fake_smtp(monkeypatch, script, stage="connect"):
    script = list(script)

    class FakeSMTP:
        attempts: list = []
        sent: list = []

        def __init__(self, host, port, timeout):
            FakeSMTP.attempts.append({"host": host, "port": port, "timeout": timeout})
            self.error = script.pop(0) if script else None
            if self.error is not None and stage == "connect":
                raise self.error

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            if self.error is not None and stage == "login":
                raise self.error

        def send_message(self, msg):
            if self.error is not None and stage == "send":
                raise self.error
            FakeSMTP.sent.append(msg)

    monkeypatch.setattr(sender_module.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def make_sender(waits=None, **overrides):
    fields = dict(
        host="smtp.test", port=2525, from_addr="praxis@brightsmile-demo.de",
        reply_to="team@brightsmile-demo.de", timeout=7.0,
        sleep=(waits.append if waits is not None else (lambda seconds: None)),
    )
    fields.update(overrides)
    return SmtpSender(**fields)


def test_accepted_mail_returns_accepted_and_builds_plain_text_message(monkeypatch):
    smtp = fake_smtp(monkeypatch, [None])
    result = make_sender().send(to=TO, subject=SUBJECT, body=BODY)
    assert (result.outcome, result.detail) == (Outcome.ACCEPTED, "")
    assert smtp.attempts == [{"host": "smtp.test", "port": 2525, "timeout": 7.0}]
    [msg] = smtp.sent
    assert msg["From"] == "praxis@brightsmile-demo.de"
    assert msg["To"] == TO
    assert msg["Reply-To"] == "team@brightsmile-demo.de"
    assert msg["Subject"] == SUBJECT
    assert msg.get_content_type() == "text/plain"
    assert msg.get_content_charset() == "utf-8"
    assert msg.get_content().rstrip("\n") == BODY


def test_refused_connection_is_retried_three_times_with_1_2_4_second_waits_then_failed(monkeypatch):
    smtp = fake_smtp(monkeypatch, [ConnectionRefusedError()] * 4)
    waits = []
    result = make_sender(waits).send(to=TO, subject=SUBJECT, body=BODY)
    assert (result.outcome, result.detail) == (Outcome.FAILED, "ConnectionRefusedError")
    assert waits == [1, 2, 4]
    assert len(smtp.attempts) == 4


def test_refused_connection_then_success_is_accepted(monkeypatch):
    smtp = fake_smtp(monkeypatch, [ConnectionRefusedError(), None])
    waits = []
    result = make_sender(waits).send(to=TO, subject=SUBJECT, body=BODY)
    assert result.outcome is Outcome.ACCEPTED
    assert waits == [1]
    assert len(smtp.sent) == 1


@pytest.mark.parametrize("stage", ["connect", "send"])
def test_timeout_is_never_retried_and_returns_timeout(monkeypatch, stage):
    smtp = fake_smtp(monkeypatch, [TimeoutError("timed out")] * 4, stage=stage)
    waits = []
    result = make_sender(waits).send(to=TO, subject=SUBJECT, body=BODY)
    assert (result.outcome, result.detail) == (Outcome.TIMEOUT, "TimeoutError")
    assert waits == []
    assert len(smtp.attempts) == 1


@pytest.mark.parametrize("error, stage", [
    (smtplib.SMTPAuthenticationError(535, b"authentication failed"), "login"),
    (smtplib.SMTPDataError(554, b"transaction failed"), "send"),
])
def test_auth_error_and_5xx_are_retried_then_failed(monkeypatch, error, stage):
    smtp = fake_smtp(monkeypatch, [error] * 4, stage=stage)
    waits = []
    result = make_sender(waits, user="praxis", password="secret").send(to=TO, subject=SUBJECT, body=BODY)
    assert (result.outcome, result.detail) == (Outcome.FAILED, type(error).__name__)
    assert waits == [1, 2, 4]
    assert len(smtp.attempts) == 4
    assert smtp.sent == []


@pytest.mark.parametrize("to, subject", [
    (TO + "\r\nBcc: victim@example.com", SUBJECT),
    (TO + "\nBcc: victim@example.com", SUBJECT),
    (TO, SUBJECT + "\rBcc: victim@example.com"),
])
def test_recipient_with_newline_is_refused_before_connecting(monkeypatch, to, subject):
    smtp = fake_smtp(monkeypatch, [None])
    waits = []
    result = make_sender(waits).send(to=to, subject=subject, body=BODY)
    assert result.outcome is Outcome.FAILED
    assert smtp.attempts == []
    assert waits == []


# The provider's error text often repeats the address ("550 no such user anna@..."). The detail
# is stored in the database and may be logged, so it is the exception class name and nothing else.
@pytest.mark.parametrize("error, stage", [
    (smtplib.SMTPRecipientsRefused({TO: (550, f"no such user {TO}".encode())}), "send"),
    (ConnectionRefusedError(f"refused for {TO}"), "connect"),
    (TimeoutError(f"timed out sending to {TO}"), "send"),
])
def test_detail_never_contains_the_recipient_address(monkeypatch, error, stage):
    fake_smtp(monkeypatch, [error] * 4, stage=stage)
    result = make_sender().send(to=TO, subject=SUBJECT, body=BODY)
    assert result.detail == type(error).__name__
    assert TO not in result.detail
    injected = make_sender().send(to=TO + "\n", subject=SUBJECT, body=BODY)
    assert TO not in injected.detail
