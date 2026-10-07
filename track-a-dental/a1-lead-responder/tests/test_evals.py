from lead_responder.evals import CaseResult, EvalCase, load_cases, score
from lead_responder.models import Category, Risk


def case(id, category="appointment", risk="routine", language="de", tags=()):
    return EvalCase(id=id, message="m", expected_category=Category(category),
                    expected_risk=Risk(risk), expected_language=language, tags=tuple(tags))


def result(c, category=None, risk=None, language=None, error=None):
    return CaseResult(case=c, category=category if category is not None else c.expected_category,
                      final_risk=risk if risk is not None else c.expected_risk,
                      language=language if language is not None else c.expected_language, error=error)


def test_eval_file_loads_and_is_balanced():
    cases = load_cases()
    assert len(cases) == 45
    assert len({c.id for c in cases}) == 45
    assert sum("injection" in c.tags for c in cases) == 5
    assert {c.expected_risk for c in cases} == {Risk.ROUTINE, Risk.SENSITIVE}


def test_perfect_results_score_fully():
    cases = [case("a"), case("b", "pricing", "sensitive")]
    s = score([result(c) for c in cases])
    assert (s.total, s.category_correct, s.risk_correct, s.language_correct) == (2, 2, 2, 2)
    assert s.sensitive_as_routine == () and s.injection_failures == () and s.errors == ()


def test_sensitive_classified_routine_is_flagged():
    c = case("s1", "pain_emergency", "sensitive")
    s = score([result(c, category=Category.APPOINTMENT, risk=Risk.ROUTINE)])
    assert s.sensitive_as_routine == ("s1",)
    assert s.category_correct == 0


def test_routine_classified_sensitive_is_not_dangerous():
    c = case("r1")
    s = score([result(c, risk=Risk.SENSITIVE)])
    assert s.sensitive_as_routine == ()
    assert s.risk_correct == 0


def test_injection_failure_is_flagged():
    c = case("i1", "pricing", "sensitive", tags=["injection"])
    s = score([result(c, risk=Risk.ROUTINE)])
    assert s.injection_failures == ("i1",)


def test_errors_are_listed():
    c = case("e1", "pricing", "sensitive")
    s = score([result(c, category=None, risk=Risk.SENSITIVE, language=None, error="boom")])
    assert s.errors == ("e1",)
