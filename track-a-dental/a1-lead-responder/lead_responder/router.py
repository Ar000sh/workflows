"""The safety decision: plain Python, no AI, fully testable.

Given the classifier's output, decide which replies to create.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum

from lead_responder.config import SUPPORTED_LANGUAGES
from lead_responder.models import Category, Classification, Risk

COMBINED_LANGUAGE = "de+en"
AUTO_REPLY_MODES = ("always", "outside_hours")

ALWAYS_SENSITIVE = frozenset({
    Category.PAIN_EMERGENCY,
    Category.MEDICAL_QUESTION,
    Category.COMPLAINT,
    Category.PRICING,
    Category.OTHER,
})


# Keyword safety net: plain code with the last word on pain/injury, in case the classifier misses it.
# Deliberately broad. A false alarm only sends a draft to staff (with emergency info); a miss would
# auto-reply to someone in pain. Matches word stems, case-insensitive, German and English.
_URGENT_SIGNS = re.compile(
    r"schmerz|\bweh\b|wehtun|pocht|\bblut|schwell|geschwollen|schwoll|notfall|notdienst|\bunfall"
    r"|fieber|atemnot|luftnot|abgebrochen|ausgeschlagen|herausgefallen|abgesplittert|\b112\b"
    r"|\bpain|\bach(?:e|es|ing)\b|\bhurt|\bbleed|\bblood|swell|swollen|emergency|accident|fever"
    r"|broken|chipped|cracked|knocked out|fell out"
    r"|wackel|locker|schluck|atem|schwindel|\bloose\b|swallow|breath|dizz",
    re.IGNORECASE,
)


def mentions_urgent_signs(text: str) -> bool:
    return _URGENT_SIGNS.search(text) is not None


class Action(StrEnum):
    AUTO_REPLY = "auto_reply"
    ACKNOWLEDGMENT = "acknowledgment"
    DRAFT = "draft"


@dataclass(frozen=True)
class Decision:
    risk: Risk
    actions: tuple[Action, ...]
    language: str  # "de", "en", or "de+en" (combined acknowledgment)
    emergency: bool
    language_unsupported: bool
    needs_manual_review: bool


def normalize_language(code: str) -> str:
    return code.strip().lower().replace("_", "-").split("-")[0]


def decide(
    classification: Classification | None,
    *,
    auto_reply_mode: str = "always",
    practice_open: bool = False,
    urgent_signs: bool = False,
) -> Decision:
    if auto_reply_mode not in AUTO_REPLY_MODES:
        raise ValueError(f"auto_reply_mode must be one of {AUTO_REPLY_MODES}, got {auto_reply_mode!r}")

    # Route 1: classifier failed. We know nothing about the message: acknowledge in both
    # languages, include emergency info in case it was about pain, and let a human take over.
    if classification is None:
        return Decision(Risk.SENSITIVE, (Action.ACKNOWLEDGMENT,), COMBINED_LANGUAGE,
                        emergency=True, language_unsupported=False, needs_manual_review=True)

    language = normalize_language(classification.language)
    # The keyword net (urgent_signs) can only make things safer: it forces emergency info and Route 3.
    emergency = classification.category is Category.PAIN_EMERGENCY or urgent_signs

    # Route 2: language is not DE/EN. Acknowledge in both languages, no AI draft; staff handle it.
    if language not in SUPPORTED_LANGUAGES:
        return Decision(Risk.SENSITIVE, (Action.ACKNOWLEDGMENT,), COMBINED_LANGUAGE,
                        emergency=emergency, language_unsupported=True, needs_manual_review=False)

    # Route 3: sensitive (model says so, the category is always sensitive whatever the model says,
    # or the keyword net fired). Instant acknowledgment for the patient + AI draft for staff to approve.
    if classification.risk is Risk.SENSITIVE or classification.category in ALWAYS_SENSITIVE or urgent_signs:
        return Decision(Risk.SENSITIVE, (Action.ACKNOWLEDGMENT, Action.DRAFT), language,
                        emergency=emergency, language_unsupported=False, needs_manual_review=False)

    # Route 4: routine, but the practice wants staff to answer during opening hours
    # (AUTO_REPLY_MODE=outside_hours). The patient gets the fixed acknowledgment (no AI paragraph);
    # staff get an AI draft so they only review and send. The dashboard lists these as routine, below sensitive.
    if auto_reply_mode == "outside_hours" and practice_open:
        return Decision(Risk.ROUTINE, (Action.ACKNOWLEDGMENT, Action.DRAFT), language,
                        emergency=False, language_unsupported=False, needs_manual_review=False)

    # Route 5: routine. Auto-reply straight away (the pipeline still guardrail-checks the text
    # and falls back to downgrade_failed_auto_reply below if it fails).
    return Decision(Risk.ROUTINE, (Action.AUTO_REPLY,), language,
                    emergency=False, language_unsupported=False, needs_manual_review=False)


def downgrade_failed_auto_reply(decision: Decision) -> Decision:
    """An auto-reply failed or broke a guardrail: treat the lead as sensitive instead."""
    return replace(decision, risk=Risk.SENSITIVE, actions=(Action.ACKNOWLEDGMENT, Action.DRAFT),
                   needs_manual_review=True)
