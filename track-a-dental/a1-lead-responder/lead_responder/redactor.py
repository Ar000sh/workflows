"""Data minimization: contact details never reach the AI.

Emails and phone numbers inside the message text become [EMAIL] / [PHONE].
Dates and times (e.g. 12.10.2026, 10:30, 12. 10. 2026, 2026.10.12) are preserved.
Names in free text are NOT removed — that cannot be done reliably (documented limitation).
"""
import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Candidate: optional leading paren, optional +/00, then digits mixed with spaces / ( ) . - ; checked further below.
_PHONE_CANDIDATE = re.compile(r"(?<![\w.])(?:\()?(?:\+|00)?\d[\d\s/().-]{5,}\d(?!\w)")
_DATE = re.compile(r"^\d{1,2}\.\d{1,2}\.(\d{2}|\d{4})$")
_MIN_PHONE_DIGITS = 7

# Patterns to preserve before phone redaction (dates and times with optional spaces around separators)
_DATE_PATTERNS = [
    # d.m.yyyy or d.m.yy with optional spaces around dots
    re.compile(r"\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{2,4}"),
    # yyyy.m.d format
    re.compile(r"\d{4}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}"),
]
_TIME_PATTERN = re.compile(r"\d{1,2}:\d{2}")


def redact(text: str) -> str:
    # Replace dates and times with opaque placeholders (no digits, so they won't match phone regex)
    preserved = {}
    counter = [0]  # Use list to allow modification in nested function

    def replace_with_placeholder(match):
        placeholder = f"REDACT_PRESERVE_{counter[0]:04d}"
        counter[0] += 1
        preserved[placeholder] = match.group(0)
        return placeholder

    # Preserve times first (they're more specific)
    text = _TIME_PATTERN.sub(replace_with_placeholder, text)

    # Preserve dates
    for pattern in _DATE_PATTERNS:
        text = pattern.sub(replace_with_placeholder, text)

    # Redact emails and phones (won't match the opaque placeholders)
    text = _EMAIL.sub("[EMAIL]", text)
    text = _PHONE_CANDIDATE.sub(_phone_or_keep, text)

    # Restore preserved content
    for placeholder, original in preserved.items():
        text = text.replace(placeholder, original)

    return text


def _phone_or_keep(match: re.Match) -> str:
    candidate = match.group(0)
    digits = sum(ch.isdigit() for ch in candidate)
    if digits < _MIN_PHONE_DIGITS or _DATE.match(candidate.strip()):
        return candidate
    return "[PHONE]"
