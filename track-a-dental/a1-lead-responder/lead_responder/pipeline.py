"""Layers 1–3 wired together: one inquiry in, classification + reply texts out.

No database and no sending yet (that is layer 4). Every AI failure ends in
human review, never in a wrong auto-reply.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime

from lead_responder import classifier, drafter, guardrails, templates
from lead_responder.config import AUTO_REPLY_MODE, MAX_MESSAGE_CHARS
from lead_responder.models import Classification, Risk
from lead_responder.practice import Practice
from lead_responder.redactor import redact
from lead_responder.router import (
    ALWAYS_SENSITIVE, Action, Decision, decide, downgrade_failed_auto_reply, mentions_urgent_signs,
)
from shared.llm import LLMClient, LLMError

log = logging.getLogger(__name__)

EVENT_CLASSIFIER_FAILED = "classifier failed"
EVENT_MESSAGE_TOO_LONG = "message too long for AI"
EVENT_AUTO_REPLY_FAILED = "auto-reply failed"          # API error
EVENT_SAFE_INFO_DROPPED = "safe-info dropped"          # API error
EVENT_SAFE_INFO_REJECTED = "safe-info rejected"        # guardrail violation
EVENT_AUTO_REPLY_REJECTED = "auto-reply rejected"      # guardrail violation
EVENT_DRAFT_FAILED = "draft failed"
EVENT_KEYWORD_OVERRIDE = "keyword safety net"          # message mentions pain/injury, classifier said routine

_PLACEHOLDER = re.compile(r"[{}]|\[Name\]", re.IGNORECASE)
_ORDER = {Action.AUTO_REPLY: 0, Action.ACKNOWLEDGMENT: 1, Action.DRAFT: 2}


@dataclass(frozen=True)
class GeneratedReply:
    kind: Action
    body: str               # what would be sent (name filled in)
    ai_body: str | None     # the AI's original text, None if no AI text was used
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PipelineResult:
    classification: Classification | None
    decision: Decision
    replies: tuple[GeneratedReply, ...]
    events: tuple[str, ...]
    cost_usd: float


class _Run:
    """Collects cost and events while one inquiry is processed."""

    def __init__(self) -> None:
        self.cost = 0.0
        self.events: list[str] = []

    def add_cost(self, result) -> None:
        self.cost += result.cost_usd or 0.0

    def event(self, kind: str, detail: str) -> None:
        text = f"{kind}: {detail}"
        log.warning(text)
        self.events.append(text)


def process_inquiry(
    *,
    llm: LLMClient,
    practice: Practice,
    faq: str,
    name: str,
    message: str,
    now: datetime,
    auto_reply_mode: str = AUTO_REPLY_MODE,
) -> PipelineResult:
    if not message.strip():
        raise ValueError("message is empty")
    run = _Run()
    practice_open = practice.is_open(now)
    redacted = redact(message)  # the only form of the message the AI ever sees
    classification = _classify(llm, redacted, message, run)
    urgent_signs = mentions_urgent_signs(redacted)
    decision = decide(classification, auto_reply_mode=auto_reply_mode, practice_open=practice_open,
                      urgent_signs=urgent_signs)
    if (urgent_signs and classification is not None and classification.risk is Risk.ROUTINE
            and classification.category not in ALWAYS_SENSITIVE):
        run.event(EVENT_KEYWORD_OVERRIDE, "message mentions pain or injury but the classifier said routine")
    replies: list[GeneratedReply] = []

    if Action.AUTO_REPLY in decision.actions:
        text = _generate(lambda: drafter.auto_reply(
            llm, redacted_message=redacted, language=decision.language, practice=practice, faq=faq,
        ), run, EVENT_AUTO_REPLY_FAILED)
        violations = guardrails.check(text, practice, require_doctolib=True) if text else []
        if text and not violations:
            replies.append(GeneratedReply(Action.AUTO_REPLY, drafter.fill_name(text, name), text))
        else:
            if text:
                # Keep the failed text as a draft so staff can see and fix it.
                run.event(EVENT_AUTO_REPLY_REJECTED, "; ".join(violations))
                replies.append(GeneratedReply(Action.DRAFT, drafter.fill_name(text, name), text, tuple(violations)))
            decision = downgrade_failed_auto_reply(decision)

    if Action.ACKNOWLEDGMENT in decision.actions:
        replies.append(_acknowledgment(llm, redacted, name, classification, decision, practice, faq,
                                       practice_open, run))

    if Action.DRAFT in decision.actions and not any(r.kind is Action.DRAFT for r in replies):
        text = _generate(lambda: drafter.staff_draft(
            llm, redacted_message=redacted, language=decision.language, practice=practice, faq=faq,
            emergency=decision.emergency,
        ), run, EVENT_DRAFT_FAILED)
        if text:
            warnings = guardrails.check(text, practice, require_doctolib=False)
            replies.append(GeneratedReply(Action.DRAFT, drafter.fill_name(text, name), text, tuple(warnings)))
        else:
            decision = replace(decision, needs_manual_review=True)

    replies.sort(key=lambda reply: _ORDER[reply.kind])
    return PipelineResult(classification, decision, tuple(replies), tuple(run.events), run.cost)


def _classify(llm, redacted: str, message: str, run: _Run) -> Classification | None:
    if len(message) > MAX_MESSAGE_CHARS:
        run.event(EVENT_MESSAGE_TOO_LONG, f"{len(message)} characters")
        return None
    try:
        result = classifier.classify(llm, redacted)
    except LLMError as error:
        run.event(EVENT_CLASSIFIER_FAILED, str(error))
        return None
    run.add_cost(result)
    return result.value


def _generate(call, run: _Run, failure_event: str) -> str | None:
    try:
        result = call()
    except LLMError as error:
        run.event(failure_event, str(error))
        return None
    run.add_cost(result)
    return result.value.strip()


def _acknowledgment(llm, redacted, name, classification, decision, practice, faq, practice_open, run) -> GeneratedReply:
    safe = None
    # Only sensitive leads get the AI safe-info paragraph. A routine lead in outside_hours mode is answered
    # by staff during opening hours (with an AI draft), so the fixed template alone is enough.
    if classification is not None and not decision.language_unsupported and decision.risk is Risk.SENSITIVE:
        text = _generate(lambda: drafter.safe_info(
            llm, redacted_message=redacted, language=decision.language, practice=practice, faq=faq,
        ), run, EVENT_SAFE_INFO_DROPPED)
        if text and text.strip().rstrip(".!").casefold() != drafter.NO_SAFE_INFO.casefold():
            violations = guardrails.check(text, practice, require_doctolib=False)
            if _PLACEHOLDER.search(text):
                violations = [*violations, "contains a placeholder"]
            if violations:
                run.event(EVENT_SAFE_INFO_REJECTED, "; ".join(violations))
            else:
                safe = text
    body = templates.acknowledgment(
        decision.language, name=name, practice=practice, emergency=decision.emergency,
        practice_open=practice_open, safe_info=safe,
    )
    return GeneratedReply(Action.ACKNOWLEDGMENT, body, safe)
