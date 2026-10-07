"""Data minimization: contact details never reach the AI.

Emails and phone numbers inside the message text become [EMAIL] / [PHONE].
Names in free text are NOT removed — that cannot be done reliably (documented limitation).
"""
import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Candidate: optional +/00, then digits mixed with spaces / ( ) . - ; checked further below.
_PHONE_CANDIDATE = re.compile(r"(?<![\w.])(?:\+|00)?\d[\d\s/().-]{5,}\d(?!\w)")
_DATE = re.compile(r"^\d{1,2}\.\d{1,2}\.(\d{2}|\d{4})$")
_MIN_PHONE_DIGITS = 7


def redact(text: str) -> str:
    text = _EMAIL.sub("[EMAIL]", text)
    return _PHONE_CANDIDATE.sub(_phone_or_keep, text)


def _phone_or_keep(match: re.Match) -> str:
    candidate = match.group(0)
    digits = sum(ch.isdigit() for ch in candidate)
    if digits < _MIN_PHONE_DIGITS or _DATE.match(candidate.strip()):
        return candidate
    return "[PHONE]"
