"""Run one inquiry through the whole layer 1–3 pipeline with REAL Claude calls.

uv run python -m lead_responder.scripts.try_inquiry --name Anna --message "Haben Sie samstags geöffnet?"
uv run python -m lead_responder.scripts.try_inquiry --name Anna --message "Starke Zahnschmerzen" --at 2026-10-04T20:00
"""
from dotenv import load_dotenv

load_dotenv()  # before importing config, so .env overrides apply

import argparse  # noqa: E402
import sys  # noqa: E402
from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from lead_responder.drafter import load_faq  # noqa: E402
from lead_responder.pipeline import process_inquiry  # noqa: E402
from lead_responder.practice import load_practice  # noqa: E402
from shared.llm import LLMClient  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="")
    parser.add_argument("--message", required=True)
    parser.add_argument("--at", help="practice-local time, e.g. 2026-10-05T20:00 (default: now)")
    args = parser.parse_args()

    practice = load_practice()
    tz = ZoneInfo(practice.timezone)
    now = datetime.fromisoformat(args.at).replace(tzinfo=tz) if args.at else datetime.now(tz)

    result = process_inquiry(llm=LLMClient(), practice=practice, faq=load_faq(),
                             name=args.name, message=args.message, now=now)

    c = result.classification
    print(f"Classification: {c.model_dump(mode='json') if c else 'FAILED'}")
    d = result.decision
    print(f"Decision: risk={d.risk} actions={[a.value for a in d.actions]} language={d.language} "
          f"emergency={d.emergency} manual_review={d.needs_manual_review} practice_open={practice.is_open(now)}")
    for event in result.events:
        print(f"Event: {event}")
    for reply in result.replies:
        print(f"\n===== {reply.kind.value} =====\n{reply.body}")
        for warning in reply.warnings:
            print(f"  WARNING: {warning}")
    print(f"\nCost: ${result.cost_usd:.5f}")


if __name__ == "__main__":
    main()
