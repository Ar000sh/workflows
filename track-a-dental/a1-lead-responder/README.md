# A1 — BrightSmile Lead Responder

Website inquiries for a (fictional) dental practice in Hannover: routine questions get an
instant AI reply; sensitive ones get an instant acknowledgment plus an AI draft for staff.
Spec: `docs/superpowers/specs/2026-10-05-a1-lead-responder-design.md`

**Status:** layers 1–3 (core logic), layer 4a (database, repository, retention) and layer 4b (intake API, email sender, sweep job). No staff login, audit log or dashboard yet (4c, 4d).

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
| Database tests only | `uv run pytest track-a-dental/a1-lead-responder/tests/db` (skipped, not failed, if Postgres is not running; the end-to-end tests also skip if Mailpit is not running) |
| DSGVO retention job | `uv run python -m lead_responder.scripts.run_retention` |
| Intake API (layer 4b) | `uv run uvicorn lead_responder.api.main:app` (port 8000; `GET /health` answers `{"status":"ok"}`) |
| Post a test lead (costs about $0.01: the real pipeline calls Claude) | see "Try the API" below |
| Sweep job (layer 4b): resend stuck acknowledgments, send unsent first replies, acknowledge leads with no reply | `uv run python -m lead_responder.scripts.run_sweep` |
| Read the mails that were "sent" | Mailpit at http://localhost:8025 |

## Data layer (layer 4a)
- **Tables** (`lead_responder/db/models.py`, SQLAlchemy ORM; changes only through Alembic migrations in `alembic/`): `leads`, `replies`, `staff_users`, `audit_log`. Status and kind values are enforced by CHECK constraints in the database too. `staff_users` and `audit_log` exist but get their code in 4c.
- **Repository** (`lead_responder/repository.py`, one transaction per method; retention commits once per batch): `create_lead_if_new` (the same email + message within 2 minutes returns the existing lead instead of a second one; a Postgres advisory lock makes this safe for simultaneous submissions; stores the normalized `mailbox_key`), `get_lead`, `get_reply`, `set_classification` (only while the lead is `new`, so a late pipeline result is discarded), `set_lead_status`, `acknowledge_lead` (a lead's first reply, acknowledgment or auto-reply: the status move to `acknowledged` and the reply insert in one transaction under a row lock, so exactly one writer wins), `suppress_lead` (per-address limit tripped: `new -> acknowledged`, flagged, `send_suppressed`, no reply), `flag_for_review`, `add_reply`, `set_reply_status`, `replies_for`, `count_leads_for_mailbox_since` / `count_leads_since` (`created_at >= since`, for the limits), `stuck_sending_replies` / `fail_stuck_reply` / `stale_drafted_first_replies` / `leads_without_first_reply` (for the sweep), `anonymize_older_than`. Only timezone-aware datetimes are accepted; lead form text is validated and cleaned before it gets here by `LeadIn` (`api/schemas.py`, limits in `limits.py`), and the database CHECKs reject anything that slips through (`IntegrityError`; NUL raises `DataError`). Status changes follow `LEAD_TRANSITIONS` / `REPLY_TRANSITIONS` (`db/models.py`) and are one conditional UPDATE (`WHERE status IN` the allowed sources), so `sent` and `rejected` replies are final; a send starts with a claim (`set_reply_status(..., SENDING)`, which also stamps `claimed_at` and counts `send_attempts`), so two sessions cannot both send the same reply; a refused move raises `InvalidTransition` (a `ValueError`).
- **Retention** (`lead_responder/retention.py`, `RETENTION_DAYS`, default 90): leads older than the limit lose name, email, phone, message, the duplicate hash, the normalized address (`mailbox_key`, since 4b) and the AI's free-text reason (`ai_reason`), and their reply texts and reply errors (`replies.error`, may hold an address) are blanked; category, risk, urgency, language, statuses and timestamps stay, so statistics survive. It works in batches of 5000 leads (one short transaction each, safe to stop and re-run). There is no clean-up pass afterwards: personal data cannot re-enter an anonymized lead, because `add_reply`, `set_classification`, `acknowledge_lead` and `suppress_lead` check the lead under a row lock (they wait for a retention batch that is running, then refuse with `ValueError`; `add_reply` takes `FOR SHARE`, the other three take `FOR NO KEY UPDATE` because they update the lead row, and two `FOR SHARE` holders updating the same row would deadlock) and `set_reply_status` drops an error text for an anonymized lead (the status still moves). A later retention run returns 0 and moves no timestamps; two runs at once share the batches, each lead handled exactly once. `batch_size` is capped at 60000 (Postgres allows 65535 bind parameters per statement). `get_lead` and `replies_for` reload from the database and end their transaction like the write methods.
- **Why it sells:** the duplicate rule stops double replies (and double API cost) when a patient double-clicks submit, and automatic deletion after a fixed period is the storage-limitation answer a German practice needs under DSGVO. Concrete ROI math comes with the full A1 in the later layers.
- Dev database `leads` and test database `leads_test` on host port 5433 (user and password `leads`; override with `DATABASE_URL`, `DATABASE_URL_TEST`).

Checked on 2026-10-24 (end of 4b): `alembic upgrade head` on the dev database ends at revision `e93e6e744ccc (head)`; `run_sweep` prints `Retried 0 stuck acknowledgment(s), sent 0 unsent first reply(ies), sent 0 fallback acknowledgment(s), left 0 for staff.` on the dev database; `uvicorn lead_responder.api.main:app` answers `GET /health` with `{"status":"ok"}`; `tests/db` gives 197 passed; the full suite gives 530 passed in about 43 s with Postgres and Mailpit up. With an unreachable test database (`DATABASE_URL_TEST=postgresql+psycopg://leads:leads@localhost:5999/leads_test`): 334 passed, 196 skipped in 13.4 s.

## Intake and sending (layer 4b)
Spec: `docs/superpowers/specs/2026-10-24-a1-layer-4b-intake-and-sender-design.md`. What happens to one form post:
1. **`POST /leads`** (`api/app.py`): the body is refused with `413` above `MAX_BODY_BYTES`; `LeadIn` (`api/schemas.py`) checks consent, email format and the field limits from `limits.py` (`422`, the body never echoes what was sent). The answer is always `202 {"status": "received"}`, for a new lead, a duplicate and a rate-limited lead alike, so nobody can probe which addresses exist.
2. **`IntakeService.receive`** (while the form waits): saves the lead (`create_lead_if_new`; a duplicate stops here), then counts leads for the same mailbox (`limits.mailbox_key`: lower-case, `+tag` cut, Gmail dots ignored), the new one included. Over `MAX_LEADS_PER_EMAIL_HOUR` or `MAX_LEADS_PER_EMAIL_DAY` the lead is `send_suppressed`: saved and flagged, no AI, no email.
3. **`IntakeService.process`** (a background task after the answer): over `MAX_AI_LEADS_PER_HOUR`, or with all `MAX_CONCURRENT_PIPELINES` busy, or when the pipeline takes longer than `PIPELINE_DEADLINE_SECONDS` or raises, the lead gets the **fixed DE+EN acknowledgment** with the emergency numbers and is flagged for staff. Otherwise the pipeline result is written (classification, first reply through `acknowledge_lead`, staff draft as `drafted`).
4. **`ReplyService.send`**: claims the reply (`-> sending`, committed before anything is sent, so two workers never send one reply twice), sends through `SmtpSender` with no database transaction open, then records `sent`, `failed` (known not sent: the exception class name is stored, never the text) or leaves it `sending` (timeout: may have arrived). Drafts are only sent after staff approval (4d).
5. **`ReplyService.sweep`** (`scripts/run_sweep.py`, run by a scheduler): acknowledgments stuck in `sending` longer than `SENDING_TIMEOUT_MINUTES` are resent, at most `MAX_SEND_ATTEMPTS` claims in all; first replies left `drafted` and leads with no first reply after `NO_REPLY_SWEEP_MINUTES` are sent / get the fixed acknowledgment. Drafts and auto-replies stuck in `sending` are never resent automatically (a second medical reply is not harmless); staff decide in 4d.

**Try the API** (Postgres and Mailpit running, `alembic upgrade head` done, `ANTHROPIC_API_KEY` in `.env`): start `uv run uvicorn lead_responder.api.main:app`, then in a second terminal
```bash
curl -i -X POST http://127.0.0.1:8000/leads -H "content-type: application/json" \
  -d '{"name": "Anna Test", "email": "anna@example.com", "phone": null, "message": "Haben Sie samstags geöffnet?", "consent": true}'
```
and open Mailpit (http://localhost:8025) to read the reply. This calls Claude (about $0.01 per lead).

**Scheduler:** nothing runs `run_sweep` or `run_retention` on its own yet. Recommended: the sweep every few minutes, retention daily; the scheduler (cron or a Docker job on the VPS, Windows Task Scheduler for a local demo, an n8n schedule in layers 5-6) and an alert when a job fails come with the deployment layer. Both scripts are safe to run twice, also at the same time.

**Settings** (env or `.env`, all listed with defaults in `.env.example`): `SMTP_HOST`, `SMTP_PORT`, `SMTP_STARTTLS`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_TIMEOUT_SECONDS`, `MAIL_FROM` (empty = the practice email); `MAX_LEADS_PER_EMAIL_HOUR` (3), `MAX_LEADS_PER_EMAIL_DAY` (10), `MAX_AI_LEADS_PER_HOUR` (60), `MAX_CONCURRENT_PIPELINES` (10), `PIPELINE_DEADLINE_SECONDS` (45); `SENDING_TIMEOUT_MINUTES` (10), `MAX_SEND_ATTEMPTS` (3), `NO_REPLY_SWEEP_MINUTES` (5); `MAX_BODY_BYTES` (32768), `CORS_ORIGINS` (empty = no browser origin).

**Logs** carry ids and event names only (never email, name, phone, message or reply text); `tests/db/test_logging.py` checks this with canary leads through the whole flow. Pipeline event details (for example which guardrail failed) stay in `PipelineResult.events`, which the eval and try scripts print locally.

**Why it sells:** the patient gets an answer within seconds even when the AI is slow, down or over budget; a mail never goes out twice; one visitor cannot use the form to flood someone's inbox; and nothing personal ends up in log files that outlive the 90-day retention.

## Pre-commit gate timing
`git commit` runs the whole suite (limit 90 s, see `.claude/hooks/precommit.py`). Measured on 2026-10-24 at the end of 4b: 43 s for the gate (530 tests with Postgres and Mailpit up); earlier 4b runs took 35-60 s and once 91 s. With the DB tests skipped the suite takes about 13 s. **The gate is close to its limit:** if it hits 90 s, do not bypass it; Tariq decides whether to change the rule (for example run only the relevant tests). The gate prints pytest's summary line and warns when tests were skipped.

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
