"""Deterministic checks on AI-written text. Code, not AI, has the last word.

Conservative on purpose: a false alarm only means a human looks at the text.
"""
from __future__ import annotations

import re

from lead_responder.practice import Practice
from lead_responder.redactor import redact

# Matches prices in various formats:
#   Amount-first: "80 €", "80,- €", "80 Euros", "ca. 80 Euro"
#   Currency-first: "€ 99", "EUR80", "Eur. 80", "EUR 20"
# Word boundaries and lack of currency markers prevent false positives on dates, URLs, and words like "Europa".
_PRICE = re.compile(
    r"\d[\d.,]*(,-)?[\s]*(€|eur\b|euros?\b)"  # Amount-first: "80 €", "80,- €", "80 Euros"
    r"|"
    r"(€\s?|\beur\.?\s?|\beuros?\s?)\d",  # Currency-first: "€ 80", "EUR80", "Eur. 80"
    re.IGNORECASE
)

DIAGNOSIS_TERMS = (
    "diagnose", "diagnosis",
    "sie haben wahrscheinlich", "you probably have",
    "das ist vermutlich", "this is probably",
    "karies", "cavity",
    "abszess", "abscess",
    "parodontitis", "periodontitis",
    "entzündung", "inflammation",
    "infektion", "infection",
    "gingivitis", "pulpitis", "zyste", "cyst", "tumor", "krebs", "cancer",
)

_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_URL_TRAILING = ".,;:!?)"
# Any brace, or the [Name] placeholder style: a leftover means the text was not filled in properly.
_PLACEHOLDER = re.compile(r"[{}]|\[name\]", re.IGNORECASE)


# Returns a list of problems found in an AI-written text (empty list = passed). Checks:
# the booking link is present as a whole link (auto-replies only), no other links, no phone numbers,
# emails or ID numbers except the practice's own, no leftover placeholders, no prices, no diagnosis
# wording, and (if max_words is given) not too long.
# Pass the FINAL text (name already filled in): that is what the patient would read.
# What the pipeline does with a failure: auto-reply is NOT sent (becomes a staff draft, lead downgraded
# to sensitive); safe-info paragraph is dropped; staff draft is kept and staff see the warnings.
# It cannot verify facts (e.g. invented opening hours) or catch prices written in words: that is what
# the prompt rules, the evals and the human review are for.
def check(text: str, practice: Practice, *, require_doctolib: bool, max_words: int | None = None) -> list[str]:
    violations: list[str] = []
    links = [link.rstrip(_URL_TRAILING) for link in _URL.findall(text)]
    if require_doctolib and practice.doctolib_url not in links:
        violations.append("missing Doctolib link")
    foreign = [link for link in links if link != practice.doctolib_url]
    if foreign:
        violations.append("contains a link that is not the booking link: " + ", ".join(foreign))
    # Without the practice's own numbers and links, nothing that looks like a phone number, email or ID may remain.
    leftover = text
    for allowed in (practice.phone, practice.notdienst_phone):
        leftover = leftover.replace(allowed, " ")
    leftover = _URL.sub(" ", leftover)
    if redact(leftover) != leftover:
        violations.append("contains a phone number, email or ID that is not the practice's")
    if _PLACEHOLDER.search(text):
        violations.append("contains a placeholder")
    if _PRICE.search(text):
        violations.append("mentions a price")
    lowered = text.casefold()
    found = [term for term in DIAGNOSIS_TERMS if term in lowered]
    if found:
        violations.append("diagnosis-like wording: " + ", ".join(found))
    if max_words is not None and len(text.split()) > max_words:
        violations.append(f"too long ({len(text.split())} words, limit {max_words})")
    return violations
