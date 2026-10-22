"""Layer 3 eval: run every eval inquiry through the REAL pipeline and check the AI texts.

Run:  uv run python -m lead_responder.scripts.eval_drafter
Writes all generated texts to eval_runs/drafts-<timestamp>.md for reading by hand.
Exit code 1 if the run is not valid: any classifier failure, API error while drafting, auto-reply or
safe-info paragraph rejected by guardrails, or no auto-reply produced at all.
Cost: about 45 classifier calls + 45–90 Sonnet calls (well under $1).
"""
from dotenv import load_dotenv

load_dotenv()  # before importing config, so .env overrides apply

import sys  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from lead_responder.config import CLASSIFIER_MODEL, DRAFTER_EFFORT, DRAFTER_MODEL  # noqa: E402
from lead_responder.drafter import load_faq  # noqa: E402
from lead_responder.evals import load_cases  # noqa: E402
from lead_responder.pipeline import (  # noqa: E402
    EVENT_AUTO_REPLY_FAILED, EVENT_AUTO_REPLY_REJECTED, EVENT_CLASSIFIER_FAILED, EVENT_DRAFT_FAILED,
    EVENT_SAFE_INFO_DROPPED, EVENT_SAFE_INFO_REJECTED, process_inquiry,
)
from lead_responder.practice import load_practice  # noqa: E402
from lead_responder.router import Action  # noqa: E402
from shared.llm import LLMClient  # noqa: E402

NOW = datetime(2026, 10, 5, 10, 0, tzinfo=ZoneInfo("Europe/Berlin"))  # Monday, practice open
OUT_DIR = Path(__file__).resolve().parents[2] / "eval_runs"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    llm, practice, faq = LLMClient(), load_practice(), load_faq()
    report = [f"# Drafter eval {datetime.now():%Y-%m-%d %H:%M}\n",
              f"Drafter model: {DRAFTER_MODEL} (effort: {DRAFTER_EFFORT or 'none'}) | classifier: {CLASSIFIER_MODEL}\n"]
    buckets = {name: [] for name in
               ("classifier_failed", "api_errors", "auto_rejected", "safe_rejected", "auto_produced")}
    total_cost = 0.0

    for case in load_cases():
        # "always" so a .env AUTO_REPLY_MODE cannot turn the guardrail gate into a no-op.
        result = process_inquiry(llm=llm, practice=practice, faq=faq, name="Alex", message=case.message,
                                 now=NOW, auto_reply_mode="always")
        total_cost += result.cost_usd

        def has(*prefixes):
            return any(e.startswith(prefixes) for e in result.events)

        if has(EVENT_CLASSIFIER_FAILED):
            buckets["classifier_failed"].append(case.id)
        if has(EVENT_AUTO_REPLY_FAILED, EVENT_SAFE_INFO_DROPPED, EVENT_DRAFT_FAILED):
            buckets["api_errors"].append(case.id)
        if has(EVENT_AUTO_REPLY_REJECTED):
            buckets["auto_rejected"].append(case.id)
        if has(EVENT_SAFE_INFO_REJECTED):
            buckets["safe_rejected"].append(case.id)
        if any(r.kind is Action.AUTO_REPLY for r in result.replies):
            buckets["auto_produced"].append(case.id)
        report.append(f"## {case.id} — expected {case.expected_category}/{case.expected_risk}\n")
        report.append(f"> {case.message}\n")
        d = result.decision
        report.append(f"Decision: {d.risk}, {[a.value for a in d.actions]}, manual review: {d.needs_manual_review}\n")
        for event in result.events:
            report.append(f"- Event: {event}")
        for reply in result.replies:
            report.append(f"\n**{reply.kind.value}**" + (f" (warnings: {list(reply.warnings)})" if reply.warnings else ""))
            report.append(f"\n```\n{reply.body}\n```\n")
        print(f"{case.id}: {[r.kind.value for r in result.replies]}")

    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"drafts-{datetime.now():%Y%m%d-%H%M}.md"
    path.write_text("\n".join(report), encoding="utf-8")

    produced = len(buckets["auto_produced"])
    valid = not (buckets["classifier_failed"] or buckets["api_errors"] or buckets["auto_rejected"]
                 or buckets["safe_rejected"]) and produced > 0
    print(f"\nRun valid: {'yes' if valid else 'NO - results cannot be trusted'}")
    print(f"Auto-replies produced (must be > 0):             {produced}")
    print(f"Classifier failures (target 0):                  {len(buckets['classifier_failed'])} {buckets['classifier_failed']}")
    print(f"API errors while drafting (target 0):            {len(buckets['api_errors'])} {buckets['api_errors']}")
    print(f"Auto-replies rejected by guardrails (target 0):  {len(buckets['auto_rejected'])} {buckets['auto_rejected']}")
    print(f"Safe-info rejected by guardrails (target 0):     {len(buckets['safe_rejected'])} {buckets['safe_rejected']}")
    print(f"Total cost: ${total_cost:.4f}")
    print(f"Full texts: {path}")
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main())
