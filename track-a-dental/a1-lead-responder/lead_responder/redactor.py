"""Data minimization: contact details never reach the AI.

Emails become [EMAIL]. Phone numbers, card numbers, IBANs and insurance numbers inside the
message text become [NUMBER]: the AI never needs to tell them apart.
Dates and times (e.g. 12.10.2026, 10:30, 12. 10. 2026, 2026.10.12) are preserved.
Names in free text are NOT removed — that cannot be done reliably (documented limitation).
"""
import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Candidate: optional leading paren, optional +/00, then digits mixed with spaces / ( ) . - ; checked further below.
_PHONE_CANDIDATE = re.compile(r"(?<![\w.])(?:\()?(?:\+|00)?\d[\d\s/().-]{5,}\d(?!\w)")
_DATE = re.compile(r"^\d{1,2}\.\d{1,2}\.(\d{2}|\d{4})$")
_MIN_PHONE_DIGITS = 7
# IBAN: 2 letters + 2 check digits + groups of 4 (spaces optional) + a short numeric tail, e.g. DE89 3704 ... 0130 00.
_IBAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?\d{1,3})?(?![A-Za-z0-9])", re.IGNORECASE)
# German Versichertennummer: one letter + 9 digits. The phone regex misses it because it starts with a letter.
_INSURANCE_ID = re.compile(r"(?<![A-Za-z0-9])[A-Z]\d{9}(?![A-Za-z0-9])", re.IGNORECASE)

# Patterns to preserve before phone redaction (dates and times with optional spaces around separators)
_DATE_PATTERNS = [
    # d.m.yyyy or d.m.yy with optional spaces around dots
    re.compile(r"(?<![\d.])\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{2,4}(?![\d.])"),
    # yyyy.m.d format
    re.compile(r"(?<![\d.])\d{4}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}(?![\d.])"),
    # d/m/yyyy and d-m-yyyy: no spaces and valid ranges only, so dashed phone numbers stay redacted
    re.compile(r"(?<![\d./-])(?:0?[1-9]|[12]\d|3[01])([/-])(?:0?[1-9]|1[0-2])\1\d{4}(?![\d/-]|\.\d)"),
    # ISO yyyy-m-d
    re.compile(r"(?<![\d./-])\d{4}-(?:0?[1-9]|1[0-2])-(?:0?[1-9]|[12]\d|3[01])(?![\d/-]|\.\d)"),
]
_TIME_PATTERN = re.compile(r"\d{1,2}:\d{2}")


# Dates and times are hidden behind placeholders only while emails and numbers are
# redacted, so the loose phone regex cannot mistake a date for a number. They are restored
# at the end because the AI needs them; [EMAIL] and [NUMBER] are never restored.
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

    # Redact emails and numbers (won't match the opaque placeholders). Specific formats first.
    text = _EMAIL.sub("[EMAIL]", text)
    text = _IBAN.sub("[NUMBER]", text)
    text = _INSURANCE_ID.sub("[NUMBER]", text)
    text = _PHONE_CANDIDATE.sub(_phone_or_keep, text)

    # Restore preserved content
    for placeholder, original in preserved.items():
        text = text.replace(placeholder, original)

    return text


def _phone_or_keep(match: re.Match) -> str:
    candidate = match.group(0)
    digits = sum(ch.isdigit() for ch in candidate)
    # looks if the digets add up to less that min number that classifies as senstive like phone number or 
    # insurence card number iban and so on or if the number represents a date like 2025 or something
    if digits < _MIN_PHONE_DIGITS or _DATE.match(candidate.strip()):
        return candidate
    return "[NUMBER]"
