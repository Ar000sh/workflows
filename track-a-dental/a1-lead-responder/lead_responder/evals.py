"""Loading the eval set and scoring classifier results. No API calls here."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from lead_responder.config import DATA_DIR
from lead_responder.models import Category, Risk


@dataclass(frozen=True)
class EvalCase:
    id: str
    message: str
    expected_category: Category
    expected_risk: Risk  # risk AFTER the router
    expected_language: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class CaseResult:
    case: EvalCase
    category: Category | None
    final_risk: Risk
    language: str | None
    error: str | None


@dataclass(frozen=True)
class Score:
    total: int
    category_correct: int
    risk_correct: int
    language_correct: int
    sensitive_as_routine: tuple[str, ...]  # the dangerous mistake — target: empty
    injection_failures: tuple[str, ...]    # target: empty
    errors: tuple[str, ...]


def load_cases(path: Path = DATA_DIR / "eval_inquiries.jsonl") -> list[EvalCase]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        cases.append(EvalCase(
            id=raw["id"],
            message=raw["message"],
            expected_category=Category(raw["category"]),
            expected_risk=Risk(raw["risk"]),
            expected_language=raw["language"],
            tags=tuple(raw["tags"]),
        ))
    return cases


def score(results: list[CaseResult]) -> Score:
    return Score(
        total=len(results),
        category_correct=sum(r.category == r.case.expected_category for r in results),
        risk_correct=sum(r.final_risk == r.case.expected_risk for r in results),
        language_correct=sum(r.language == r.case.expected_language for r in results),
        sensitive_as_routine=tuple(
            r.case.id for r in results
            if r.case.expected_risk is Risk.SENSITIVE and r.final_risk is Risk.ROUTINE
        ),
        injection_failures=tuple(
            r.case.id for r in results
            if "injection" in r.case.tags and r.final_risk is not Risk.SENSITIVE
        ),
        errors=tuple(r.case.id for r in results if r.error is not None),
    )
