# A1 — BrightSmile Lead Responder

Website inquiries for a (fictional) dental practice in Hannover: routine questions get an
instant AI reply; sensitive ones get an instant acknowledgment plus an AI draft for staff.
Spec: `docs/superpowers/specs/2026-10-05-a1-lead-responder-design.md`

**Status:** layers 1–3 (core logic) and layer 4a (database, repository, retention). No web API, sending or dashboard yet (4b onwards).

## Setup
Requires [uv](https://docs.astral.sh/uv/) to be installed.
```bash
uv sync
cp .env.example .env        # Git Bash; in PowerShell/cmd use: copy .env.example .env
# then paste your ANTHROPIC_API_KEY into .env
```

## Run
Run all commands from the repo root.

| What | Command |
|---|---|
| Unit tests (no API calls) | `uv run pytest` |
| Layer 1: first Claude call | `uv run python -m lead_responder.scripts.hello_claude` |
| One inquiry end-to-end | `uv run python -m lead_responder.scripts.try_inquiry --name Anna --message "Haben Sie samstags geöffnet?"` |
| Classifier eval | `uv run python -m lead_responder.scripts.eval_classifier` |
| Drafter eval | `uv run python -m lead_responder.scripts.eval_drafter` |
| Start Postgres + Mailpit (Docker Desktop must be running) | `docker compose -f track-a-dental/a1-lead-responder/docker-compose.yml up -d postgres mailpit` |
| Create / update the database tables | `uv run alembic -c track-a-dental/a1-lead-responder/alembic.ini upgrade head` (or `uv run alembic upgrade head` from inside `track-a-dental/a1-lead-responder/`) |
| Database tests only | `uv run pytest track-a-dental/a1-lead-responder/tests/db` (skipped, not failed, if Postgres is not running) |
| DSGVO retention job | `uv run python -m lead_responder.scripts.run_retention` |

## Data layer (layer 4a)
- **Tables** (`lead_responder/db/models.py`, SQLAlchemy ORM; changes only through Alembic migrations in `alembic/`): `leads`, `replies`, `staff_users`, `audit_log`. Status and kind values are enforced by CHECK constraints in the database too. `staff_users` and `audit_log` exist but get their code in 4c.
- **Repository** (`lead_responder/repository.py`, one transaction per method; retention commits once per batch): `create_lead_if_new` (the same email + message within 2 minutes returns the existing lead instead of a second one; a Postgres advisory lock makes this safe for simultaneous submissions), `get_lead`, `set_classification`, `set_lead_status`, `add_reply`, `set_reply_status`, `replies_for`, `anonymize_older_than`. Only timezone-aware datetimes are accepted; NUL bytes are dropped; over-long text raises `ValueError`.
- **Retention** (`lead_responder/retention.py`, `RETENTION_DAYS`, default 90): leads older than the limit lose name, email, phone, message, the duplicate hash and the AI's free-text reason (`ai_reason`), and their reply texts and reply errors (`replies.error`, may hold an address) are blanked; category, risk, urgency, language, statuses and timestamps stay, so statistics survive. It works in batches of 5000 leads (one short transaction each, safe to stop and re-run) and sweeps leftovers on leads that are already anonymized (a late send error, reply text or AI reason). The repository refuses `add_reply` and `set_classification` for an anonymized lead (`ValueError`). Running it again is safe: the second run returns 0 and moves no timestamps; it only blanks leftover text on already anonymized leads (the sweep).
- **Why it sells:** the duplicate rule stops double replies (and double API cost) when a patient double-clicks submit, and automatic deletion after a fixed period is the storage-limitation answer a German practice needs under DSGVO. Concrete ROI math comes with the full A1 in the later layers.
- Dev database `leads` and test database `leads_test` on host port 5433 (user and password `leads`; override with `DATABASE_URL`, `DATABASE_URL_TEST`).

Checked on 2026-10-23: `alembic upgrade head` on the dev database ends at revision `a30306790587 (head)`; `run_retention` prints `Anonymized 0 lead(s) older than 90 days.` on the empty dev database; `tests/db` gives 61 passed; the full suite gives 347 passed with Postgres up. Measured on 2026-10-23 with an unreachable test database (`DATABASE_URL_TEST=postgresql+psycopg://leads:leads@localhost:5999/leads_test`, Docker left running): 286 passed, 61 skipped in 10.7 s.

## Pre-commit gate timing
`git commit` runs the whole suite (limit 90 s, see `.claude/hooks/precommit.py`). Measured by the Task 5 implementer on 2026-10-23: 11-13 s for the gate (334 tests, the count at the time of that measurement; the suite has 347 tests now). Current full run on 2026-10-23 with the DB tests skipped (unreachable test DB): 286 passed, 61 skipped in 10.7 s (pytest time, 11.9 s wall). Earlier subagents reported 57 s (cold run) and 16 s; those two numbers were not measured by us. So there is headroom, but if it ever nears 90 s, ask Tariq before changing the rule. The gate prints pytest's summary line and warns when tests were skipped.

## How an inquiry flows
1. **Redact** emails and phone numbers → only the message text goes to Claude.
2. **Classify** (Haiku, structured output) → category, risk, urgency, language.
3. **Route** (plain Python) → auto-reply, or acknowledgment + staff draft. A keyword safety net (pain, bleeding, swelling, Notfall, 112, ...) forces the sensitive route even if the classifier said routine.
4. **Write** texts (Sonnet) from `data/faq.md`; acknowledgments are fixed templates plus an optional AI safe-info paragraph (guardrail-checked).
5. **Guardrails** check every AI text as the patient would read it (links, phone numbers, placeholders, prices, diagnosis words, length); a failing auto-reply goes to staff instead. The form's name is reduced to a short plain name or nothing before it is used in any reply.

## Known limitation
A refusal or `max_tokens` stop is **not** retried (the same answer would come back): the call fails at once and the lead falls back to human review. Network errors, 429/5xx and invalid structured output are retried (up to 4 attempts).

## Eval results
Run on 2026-10-22 against the live API. Eval labels, `faq.md` and `practice.yaml` reviewed by Tariq on 2026-10-23, so these scores count. (Re-run the drafter eval after the 2026-10-23 guardrail changes; not done yet.)

Classifier (`eval_classifier`, 45 inquiries incl. 5 prompt-injection cases), model `claude-haiku-4-5`, cost $0.057:
- Category accuracy 44/45, risk accuracy 45/45, language accuracy 45/45.
- Sensitive classified as routine: **0** (target 0). Injection failures: **0** (target 0). Errors: 0.
- The one miss is `i05` (expected `medical_question`). Haiku answers `pain_emergency` or `other` from run to run; both are sensitive, so routing is the same. A label to review, not a safety problem.

Drafter (`eval_drafter`), models `claude-sonnet-5-5` (effort low) + Haiku classifier, cost $0.44:
- 18 auto-replies produced, **0** rejected by guardrails; **0** safe-info paragraphs rejected; 0 API errors, 0 classifier failures.
- Manual review findings: **not done yet** (read the generated `eval_runs/drafts-*.md` first; the folder is gitignored).

Re-run after any prompt, model or router change:
`uv run python -m lead_responder.scripts.eval_classifier` and
`uv run python -m lead_responder.scripts.eval_drafter`.

## DSGVO notes
- Only redacted message text is sent to Anthropic; names, emails and phone fields stay local.
- Known limitation: names, birth dates and street addresses written inside the message text are not removed (see REVIEW.md, "Parked: redactor hardening").
- Processors a real deployment needs a DPA (AVV) with: Anthropic, the email provider, the hosting provider.
- These notes are orientation, not legal advice.
