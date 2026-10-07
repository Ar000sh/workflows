"""The safety decision: plain Python, no AI, fully testable.

Given the classifier's output, decide which replies to create.
"""
from __future__ import annotations

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
) -> Decision:
    if auto_reply_mode not in AUTO_REPLY_MODES:
        raise ValueError(f"auto_reply_mode must be one of {AUTO_REPLY_MODES}, got {auto_reply_mode!r}")

    if classification is None:
        # We know nothing about the message: acknowledge in both languages, include
        # emergency info in case it was about pain, and let a human take over.
        return Decision(Risk.SENSITIVE, (Action.ACKNOWLEDGMENT,), COMBINED_LANGUAGE,
                        emergency=True, language_unsupported=False, needs_manual_review=True)

    language = normalize_language(classification.language)
    emergency = classification.category is Category.PAIN_EMERGENCY

    if language not in SUPPORTED_LANGUAGES:
        return Decision(Risk.SENSITIVE, (Action.ACKNOWLEDGMENT,), COMBINED_LANGUAGE,
                        emergency=emergency, language_unsupported=True, needs_manual_review=False)

    if classification.risk is Risk.SENSITIVE or classification.category in ALWAYS_SENSITIVE:
        return Decision(Risk.SENSITIVE, (Action.ACKNOWLEDGMENT, Action.DRAFT), language,
                        emergency=emergency, language_unsupported=False, needs_manual_review=False)

    if auto_reply_mode == "outside_hours" and practice_open:
        return Decision(Risk.ROUTINE, (Action.DRAFT,), language,
                        emergency=False, language_unsupported=False, needs_manual_review=False)

    return Decision(Risk.ROUTINE, (Action.AUTO_REPLY,), language,
                    emergency=False, language_unsupported=False, needs_manual_review=False)


def downgrade_failed_auto_reply(decision: Decision) -> Decision:
    """An auto-reply failed or broke a guardrail: treat the lead as sensitive instead."""
    return replace(decision, risk=Risk.SENSITIVE, actions=(Action.ACKNOWLEDGMENT, Action.DRAFT),
                   needs_manual_review=True)
