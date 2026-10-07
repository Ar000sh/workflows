"""Layer 3 eval: run every eval inquiry through the REAL pipeline and check the AI texts.

Run:  uv run python -m lead_responder.scripts.eval_drafter
Writes all generated texts to eval_runs/drafts-<timestamp>.md for reading by hand.
Exit code 1 if any auto-reply broke a guardrail (it was then sent to staff instead).
Cost: about 45 classifier calls + 45–90 Sonnet calls (well under $1).
"""
from dotenv import load_dotenv

load_dotenv()  # before importing config, so .env overrides apply

import sys  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from lead_responder.drafter import load_faq  # noqa: E402
from lead_responder.evals import load_cases  # noqa: E402
from lead_responder.pipeline import EVENT_AUTO_REPLY_FAILED, EVENT_SAFE_INFO_DROPPED, process_inquiry  # noqa: E402
from lead_responder.practice import load_practice  # noqa: E402
from shared.llm import LLMClient  # noqa: E402

NOW = datetime(2026, 10, 5, 10, 0, tzinfo=ZoneInfo("Europe/Berlin"))  # Monday, practice open
OUT_DIR = Path(__file__).resolve().parents[2] / "eval_runs"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    llm, practice, faq = LLMClient(), load_practice(), load_faq()
    report = [f"# Drafter eval {datetime.now():%Y-%m-%d %H:%M}\n"]
    downgraded, dropped, total_cost = [], [], 0.0

    for case in load_cases():
        result = process_inquiry(llm=llm, practice=practice, faq=faq, name="Alex", message=case.message, now=NOW)
        total_cost += result.cost_usd
        if any(e.startswith(EVENT_AUTO_REPLY_FAILED) for e in result.events):
            downgraded.append(case.id)
        if any(e.startswith(EVENT_SAFE_INFO_DROPPED) for e in result.events):
            dropped.append(case.id)
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

    print(f"\nAuto-replies that broke a guardrail (target 0): {len(downgraded)} {downgraded}")
    print(f"Safe-info paragraphs dropped by guardrails:     {len(dropped)} {dropped}")
    print(f"Total cost: ${total_cost:.4f}")
    print(f"Full texts: {path}")
    return 1 if downgraded else 0


if __name__ == "__main__":
    sys.exit(main())
