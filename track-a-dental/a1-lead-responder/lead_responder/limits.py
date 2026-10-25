"""Lead form limits and text helpers shared by the API model and the database.

One definition, two users: `api/schemas.py` rejects too-long input with a clear error, and
`db/models.py` builds its CHECK constraints from the same numbers as the last line of defence.
"""
from __future__ import annotations

MAX_NAME, MAX_EMAIL, MAX_PHONE, MAX_MESSAGE = 200, 254, 50, 10_000


# Postgres text columns cannot hold NUL (the zero byte); drop it instead of crashing the insert.
def clean_text(value: str) -> str:
    return value.replace(chr(0), "")


# The address a mailbox really is, so "Victim+1@Example.com" and "victim@example.com" count as
# one person for the per-email rate limit. Lower-case; everything after the first "+" in the
# local part is a tag and is cut; Gmail ignores dots, so they go too, but only for Gmail
# (on other providers "a.b" and "ab" can be different people). googlemail.com is Gmail.
def mailbox_key(email: str) -> str:
    local, _, domain = email.strip().lower().rpartition("@")
    local = local.split("+", 1)[0]
    if domain == "googlemail.com":
        domain = "gmail.com"
    if domain == "gmail.com":
        local = local.replace(".", "")
    return f"{local}@{domain}"
