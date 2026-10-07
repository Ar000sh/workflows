"""Deterministic checks on AI-written text. Code, not AI, has the last word.

Conservative on purpose: a false alarm only means a human looks at the text.
"""
from __future__ import annotations

import re

from lead_responder.practice import Practice

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
)


def check(text: str, practice: Practice, *, require_doctolib: bool, emergency: bool) -> list[str]:
    violations: list[str] = []
    if require_doctolib and practice.doctolib_url not in text:
        violations.append("missing Doctolib link")
    if _PRICE.search(text):
        violations.append("mentions a price")
    lowered = text.casefold()
    found = [term for term in DIAGNOSIS_TERMS if term in lowered]
    if found:
        violations.append("diagnosis-like wording: " + ", ".join(found))
    if emergency and "112" not in text and practice.phone not in text:
        violations.append("missing emergency contact")
    return violations
