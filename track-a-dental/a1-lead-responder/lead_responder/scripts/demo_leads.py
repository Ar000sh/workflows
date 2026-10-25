"""Demo: post three fake leads through the real API with the REAL Claude and show what happened.

Run:  uv run python -m lead_responder.scripts.demo_leads            (asks before spending)
      uv run python -m lead_responder.scripts.demo_leads --yes     (no question)
      uv run python -m lead_responder.scripts.demo_leads --url http://127.0.0.1:8000
          (posts to an API you started yourself with uvicorn instead of running one in-process)

Needs: Postgres and Mailpit running, `alembic upgrade head` done, ANTHROPIC_API_KEY in .env.
Cost: about $0.03 (three leads, a few cents of Haiku and Sonnet calls). Nothing is deleted: the
leads stay in the database and the mails in Mailpit (http://localhost:8025); every run uses fresh
addresses, so earlier runs never trip the duplicate check or the per-email limit.
"""
from dotenv import load_dotenv

load_dotenv()  # before importing config, so .env overrides apply

import argparse  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from lead_responder import config  # noqa: E402
from lead_responder.db.models import Lead, Reply  # noqa: E402
from lead_responder.db.session import make_engine, make_session_factory  # noqa: E402

MAILPIT_API = "http://127.0.0.1:8025/api/v1"
WAIT_SECONDS = 90  # the pipeline has a 45 s deadline; the mail follows right after

# One scenario per route the dashboard will later show: routine -> auto-reply, sensitive -> fixed
# acknowledgment plus a staff draft, unsupported language -> combined DE+EN acknowledgment.
SCENARIOS = [
    ("routine question (auto-reply)", "Anna Routine", "Haben Sie samstags geöffnet?"),
    ("pain (acknowledgment + staff draft)", "Ben Schmerz",
     "Ich habe seit gestern starke Zahnschmerzen und mein Gesicht ist geschwollen."),
    ("Turkish (combined DE+EN acknowledgment)", "Can Türkçe", "Merhaba, randevu almak istiyorum."),
]


# What happens here: one POST per scenario to /leads (the API answers 202 at once; the AI work
# runs in the background), then we wait until each address has a mail in Mailpit and print the
# lead and its replies as the database holds them.
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", help="base URL of a running API; default: run the app in-process")
    parser.add_argument("--yes", action="store_true", help="do not ask before calling Claude")
    args = parser.parse_args()

    if not args.yes and input("This calls the real Claude (about $0.03). Continue? [y/N] ").lower() != "y":
        print("Cancelled, nothing was spent.")
        return 0

    run_id = int(time.time())
    leads = [(label, name, f"demo.{run_id}.{n}@example.com", message)
             for n, (label, name, message) in enumerate(SCENARIOS)]

    if args.url:
        post = lambda body: httpx.post(f"{args.url}/leads", json=body, timeout=30)  # noqa: E731
        close = lambda: None  # noqa: E731
    else:
        from fastapi.testclient import TestClient

        from lead_responder.api.main import build_app

        client = TestClient(build_app())
        client.__enter__()  # starts the app; __exit__ waits for running pipelines
        post = lambda body: client.post("/leads", json=body)  # noqa: E731
        close = lambda: client.__exit__(None, None, None)  # noqa: E731

    try:
        for label, name, email, message in leads:
            body = {"name": name, "email": email, "phone": None, "message": message, "consent": True}
            response = post(body)
            print(f"POST {label}: {response.status_code} {response.json()}")
        wait_for_mails([email for _, _, email, _ in leads])
    finally:
        close()

    show_results([email for _, _, email, _ in leads])
    print("\nOpen http://localhost:8025 to read the mails.")
    return 0


# Polls Mailpit until every address has at least one mail, or WAIT_SECONDS pass.
def wait_for_mails(addresses: list[str]) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        got = mails_by_address()
        if all(address in got for address in addresses):
            return
        time.sleep(1)
    print(f"(some addresses still have no mail after {WAIT_SECONDS} s; the sweep would handle them)")


def mails_by_address() -> dict[str, dict]:
    messages = httpx.get(f"{MAILPIT_API}/messages", timeout=5).json()["messages"]
    return {to["Address"]: message for message in messages for to in message["To"]}


# Reads each lead and its replies back from the database and the mail from Mailpit.
def show_results(addresses: list[str]) -> None:
    factory = make_session_factory(make_engine(config.DATABASE_URL))
    mails = mails_by_address()
    with factory() as session:
        for address in addresses:
            lead = session.scalar(select(Lead).where(Lead.email == address))
            print(f"\n=== {address}")
            if lead is None:
                print("  no lead stored")
                continue
            print(f"  lead: status={lead.status} category={lead.category} risk={lead.risk} "
                  f"language={lead.language} needs_manual_review={lead.needs_manual_review}")
            for reply in session.scalars(select(Reply).where(Reply.lead_id == lead.id).order_by(Reply.id)):
                print(f"  reply: kind={reply.kind} status={reply.status} error={reply.error}")
            mail = mails.get(address)
            if mail is None:
                print("  mail: none in Mailpit")
                continue
            text = httpx.get(f"{MAILPIT_API}/message/{mail['ID']}", timeout=5).json()["Text"]
            print(f"  mail subject: {mail['Subject']}")
            print("  mail text:\n    " + text.strip().replace("\n", "\n    ")[:900])


if __name__ == "__main__":
    sys.exit(main())
