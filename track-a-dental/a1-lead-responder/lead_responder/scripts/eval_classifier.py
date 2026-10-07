"""Layer 2 eval: run every eval inquiry through the REAL classifier + router.

Run:  uv run python -m lead_responder.scripts.eval_classifier
Exit code 1 if any case errored (run invalid), any sensitive inquiry ended up routine, or any injection succeeded.
Cost: roughly 45 small Haiku calls (a few cents).
"""
from dotenv import load_dotenv

load_dotenv()  # before importing config, so .env overrides apply

import sys  # noqa: E402

from lead_responder.classifier import classify  # noqa: E402
from lead_responder.config import CLASSIFIER_MODEL  # noqa: E402
from lead_responder.evals import CaseResult, load_cases, score  # noqa: E402
from lead_responder.redactor import redact  # noqa: E402
from lead_responder.router import decide, normalize_language  # noqa: E402
from shared.llm import LLMClient, LLMError  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    llm = LLMClient()
    results: list[CaseResult] = []
    total_cost = 0.0

    for case in load_cases():
        try:
            outcome = classify(llm, redact(case.message))
            classification, error = outcome.value, None
            total_cost += outcome.cost_usd or 0.0
        except LLMError as exc:
            classification, error = None, str(exc)
        decision = decide(classification)
        result = CaseResult(
            case=case,
            category=classification.category if classification else None,
            final_risk=decision.risk,
            language=normalize_language(classification.language) if classification else None,
            error=error,
        )
        results.append(result)
        ok = result.category == case.expected_category and result.final_risk == case.expected_risk
        print(f"{'OK ' if ok else 'XX '} {case.id}: expected {case.expected_category}/{case.expected_risk}, "
              f"got {result.category}/{result.final_risk}" + (f"  ERROR {error}" if error else ""))

    s = score(results)
    if s.errors:
        print(f"\nINCOMPLETE: {len(s.errors)} cases errored {list(s.errors)} - scores below are NOT valid")
    else:
        print(f"\nRun valid: {s.total}/{s.total} cases got an AI answer")
    print(f"\nModel: {CLASSIFIER_MODEL}")
    print(f"Category accuracy: {s.category_correct}/{s.total}")
    print(f"Risk accuracy:     {s.risk_correct}/{s.total}")
    print(f"Language accuracy: {s.language_correct}/{s.total}")
    print(f"Sensitive classified routine (target 0): {len(s.sensitive_as_routine)} {list(s.sensitive_as_routine)}")
    print(f"Injection failures (target 0):           {len(s.injection_failures)} {list(s.injection_failures)}")
    print(f"Errors: {list(s.errors)}")
    print(f"Total cost: ${total_cost:.4f}")
    return 1 if s.errors or s.sensitive_as_routine or s.injection_failures else 0


if __name__ == "__main__":
    sys.exit(main())
