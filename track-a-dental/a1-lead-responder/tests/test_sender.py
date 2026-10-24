"""SmtpSender: typed results, the retry policy (A1 spec section 7) and header-injection refusal.

No real mail is sent: smtplib.SMTP is replaced by a fake that follows a script, one entry per
connection attempt (None = this attempt works, an exception = this attempt raises it).
"""
import smtplib
import socket
import threading
import time

import pytest

from lead_responder import sender as sender_module
from lead_responder.sender import Outcome, SmtpSender

TO = "anna.patient@example.com"
SUBJECT = "Ihre Anfrage an die Zahnarztpraxis BrightSmile"
BODY = "Hallo Anna,\n\nvielen Dank. Grüße aus der Zahnärztlichen Praxis."


# Builds a fake SMTP class for one test. `script` lists what each attempt does; `stage` says
# where the exception is raised: "connect" (the constructor) or "send" (send_message).
def fake_smtp(monkeypatch, script, stage="connect", exit_error=None):
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

        # The real __exit__ sends QUIT; exit_error plays a QUIT that the server answers badly.
        def __exit__(self, *exc):
            if exit_error is not None and exc[0] is None:
                raise exit_error
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


# --- errors classified by stage (where in the conversation they happen) ---

# What real smtplib raises when a read times out mid-conversation: it catches the OSError
# (TimeoutError included) and raises SMTPServerDisconnected, with the timeout as __context__.
def disconnect_caused_by_timeout():
    error = smtplib.SMTPServerDisconnected("Connection unexpectedly closed: timed out")
    error.__context__ = TimeoutError("timed out")
    return error


# The body may already be with the server when the connection drops: outcome unknown, so it is
# TIMEOUT (reply stays "sending") and never retried, or the patient could get it several times.
@pytest.mark.parametrize("error", [
    smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
    disconnect_caused_by_timeout(),
    ConnectionResetError("reset by peer"),
])
def test_disconnect_during_send_is_timeout_and_never_retried(monkeypatch, error):
    smtp = fake_smtp(monkeypatch, [error] * 4, stage="send")
    waits = []
    result = make_sender(waits).send(to=TO, subject=SUBJECT, body=BODY)
    assert (result.outcome, result.detail) == (Outcome.TIMEOUT, type(error).__name__)
    assert len(smtp.attempts) == 1
    assert waits == []


# The server answered with a refusal: known NOT accepted, so a retry cannot duplicate anything.
@pytest.mark.parametrize("error", [
    smtplib.SMTPRecipientsRefused({TO: (550, b"no such user")}),
    smtplib.SMTPSenderRefused(553, b"sender refused", "praxis@brightsmile-demo.de"),
    smtplib.SMTPDataError(451, b"try again later"),
])
def test_refusal_during_send_is_retried_then_failed(monkeypatch, error):
    smtp = fake_smtp(monkeypatch, [error] * 4, stage="send")
    waits = []
    result = make_sender(waits).send(to=TO, subject=SUBJECT, body=BODY)
    assert (result.outcome, result.detail) == (Outcome.FAILED, type(error).__name__)
    assert len(smtp.attempts) == 4
    assert waits == [1, 2, 4]


# Before send_message nothing of the message has left: a dropped connection is safe to retry.
def test_disconnect_during_login_is_retried(monkeypatch):
    smtp = fake_smtp(monkeypatch, [smtplib.SMTPServerDisconnected("closed")] * 4, stage="login")
    waits = []
    result = make_sender(waits, user="praxis", password="secret").send(to=TO, subject=SUBJECT, body=BODY)
    assert (result.outcome, result.detail) == (Outcome.FAILED, "SMTPServerDisconnected")
    assert len(smtp.attempts) == 4
    assert waits == [1, 2, 4]


# A timeout is a timeout at any stage, even when smtplib wraps it in SMTPServerDisconnected.
def test_timeout_wrapped_in_disconnect_during_login_is_timeout(monkeypatch):
    smtp = fake_smtp(monkeypatch, [disconnect_caused_by_timeout()] * 4, stage="login")
    waits = []
    result = make_sender(waits, user="praxis", password="secret").send(to=TO, subject=SUBJECT, body=BODY)
    assert result.outcome is Outcome.TIMEOUT
    assert len(smtp.attempts) == 1
    assert waits == []


# send_message returned, so the server accepted the mail; a bad answer to QUIT changes nothing.
def test_error_after_the_message_was_accepted_is_still_accepted(monkeypatch):
    smtp = fake_smtp(monkeypatch, [None], exit_error=smtplib.SMTPResponseException(500, b"bad quit"))
    waits = []
    result = make_sender(waits).send(to=TO, subject=SUBJECT, body=BODY)
    assert result.outcome is Outcome.ACCEPTED
    assert len(smtp.attempts) == 1
    assert waits == []


# A real SMTP conversation over a local socket (no mail leaves the machine): the server reads the
# whole message, then never answers the final ".". Real smtplib turns that read timeout into
# SMTPServerDisconnected; the sender must report TIMEOUT and must NOT reconnect and send again.
# `release` lets the stalled handlers end when the test is over.
def test_real_socket_stall_after_data_is_timeout_and_sent_once():
    received = []
    release = threading.Event()
    server = socket.create_server(("127.0.0.1", 0))
    server.settimeout(0.2)

    def handle(conn):
        with conn, conn.makefile("rb") as lines:
            conn.sendall(b"220 test\r\n")
            in_data = False
            for line in lines:
                if in_data:
                    if line == b".\r\n":
                        received.append(1)
                        release.wait(5)  # stall: never send the 250 for the message
                        return
                    continue
                command = line[:4].upper()
                if command == b"DATA":
                    conn.sendall(b"354 go\r\n")
                    in_data = True
                elif command == b"QUIT":
                    conn.sendall(b"221 bye\r\n")
                    return
                else:
                    conn.sendall(b"250 ok\r\n")

    def serve():
        while not release.is_set():
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            conn.settimeout(None)
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    waits = []
    try:
        sender = SmtpSender(
            host="127.0.0.1", port=server.getsockname()[1], from_addr="praxis@example.com",
            reply_to="praxis@example.com", timeout=0.5, sleep=waits.append,
        )
        started = time.monotonic()
        result = sender.send(to=TO, subject=SUBJECT, body=BODY)
        assert result.outcome is Outcome.TIMEOUT
        assert received == [1]
        assert waits == []
        assert time.monotonic() - started < 3
    finally:
        release.set()
        server.close()
