# A1 Layer 4a: Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Postgres (and Mailpit) in Docker, the full schema from the spec, and a tested repository layer for leads and replies, including duplicate detection and DSGVO retention. Nothing in 4a is reachable from the internet and nothing calls the AI.

**Architecture:** Plain SQL migrations applied by a tiny runner; a synchronous `Repository` class over one `psycopg` connection (FastAPI endpoints in 4b are plain `def`, so sync is enough and easy to read). The pipeline from layers 1-3 stays untouched; 4b will connect it to this repository.

**Tech Stack:** Python 3.12 (uv), `psycopg[binary]` 3.x, Postgres 16 and Mailpit via Docker Compose, pytest.

**Spec:** `docs/superpowers/specs/2026-10-05-a1-lead-responder-design.md` (§5 data model, §6 DSGVO, §7 duplicates, §14 failure modes rows 1-4). Parts 4b-4e get their own plans.

## Global Constraints
- Everything lives in the existing package `track-a-dental/a1-lead-responder/lead_responder/`; tests in `.../a1-lead-responder/tests/`. Compose file at `track-a-dental/a1-lead-responder/docker-compose.yml`.
- Postgres published on **host port 5433** (5432 is often taken on Windows). Test database name `leads_test`, dev database `leads`, user and password `leads`.
- Tables and columns exactly as spec §5 (`leads`, `replies`, `staff_users`, `audit_log`), plus `replies.edited boolean NULL` and a `schema_migrations` table. All timestamps are `timestamptz`; the code accepts **timezone-aware datetimes only** (naive raises `ValueError`).
- Status values: lead `new|classified|auto_replied|acknowledged|awaiting_approval|replied|done`; reply `drafted|approved|sent|rejected|failed`; reply kind `auto_reply|acknowledgment|draft` (same strings as `router.Action`); audit action `approve|edit|reject|resend|login|logout`. Enforced by `CHECK` constraints.
- `RETENTION_DAYS` default **90**. Duplicate window **2 minutes**, same lower-cased email and same stripped message.
- SQL is always parameterized; never build SQL with string formatting of user data.
- Secrets only in `.env`; `.env.example` lists every variable name. Claude never edits `.env`.
- Never write regexes or backslashes through bash heredocs; use Write/Edit. Slow commands under `timeout`. Every commit message ends with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. The pre-commit gate runs the suite; never `--no-verify`.
- Short teaching comments above each function (what it does and why), as in `pipeline.py`.
- Claude's calls, flagged for Tariq's review: psycopg + plain SQL instead of an ORM; DB tests **skip** (not fail) when Postgres is not running; retention also blanks reply texts (spec only names lead fields).

## Review Focus
1. Two identical submissions at the same moment: exactly one lead is created (advisory lock test with two connections).
2. Hostile text: NUL byte, `Robert'); DROP TABLE leads;--`, emoji, 10,000-character message; stored literally or rejected by a `CHECK`, never an unhandled DB crash.
3. Retention: reply texts blanked too, statistics kept, idempotent, exact 90-day boundary, young leads untouched.
4. Naive datetimes and wrong status strings rejected before or by the database.
5. Postgres not running: tests skip with a message naming the fix, and the pre-commit gate prints the skip count (Task 5).

---

### Task 1: Infrastructure and schema

**Files:**
- Create: `track-a-dental/a1-lead-responder/docker-compose.yml`, `track-a-dental/a1-lead-responder/docker/initdb/01-create-test-db.sql`, `lead_responder/db.py`, `lead_responder/migrations/0001_init.sql`, `tests/db/conftest.py`, `tests/db/test_schema.py`
- Modify: `pyproject.toml` (add `psycopg[binary]>=3.2`; add `tests/db` is already under `testpaths`), `lead_responder/config.py` (add `DATABASE_URL`, `DATABASE_URL_TEST`, `RETENTION_DAYS`), `.env.example`

**Interfaces:**
- Produces: `db.connect(url: str) -> psycopg.Connection` (autocommit off, `row_factory=dict_row`); `db.migrate(conn) -> list[str]` applies unapplied `NNNN_*.sql` files in name order, records them in `schema_migrations(version text primary key, applied_at timestamptz)`, returns the versions applied this call; fixtures `db_url` (session; skips the whole DB suite with `"Postgres not reachable at ...: run 'docker compose up -d postgres' in track-a-dental/a1-lead-responder"`) and `conn` (migrates once per session, then `TRUNCATE leads, replies, staff_users, audit_log RESTART IDENTITY CASCADE` before every test).

- [ ] **Step 1: Write the failing tests** in `tests/db/test_schema.py`: `test_migrate_creates_all_tables` (the four spec tables and `schema_migrations` exist), `test_migrate_twice_is_a_noop` (second call returns `[]`), `test_status_check_constraints_reject_unknown_values` (insert a lead with status `"banana"` raises `psycopg.errors.CheckViolation`; same for reply kind, reply status, audit action), `test_lead_requires_consent_and_limits_field_lengths` (NULL `consent_at` raises `NotNullViolation`; `name` over 200 and `message` over 10000 characters raise `CheckViolation`).
- [ ] **Step 2: Run** `timeout 120 uv run pytest tests/db -q` after `docker compose up -d postgres` has NOT been started. Expected: SKIPPED with the message above (proves the skip path). Then start it (`docker compose -f track-a-dental/a1-lead-responder/docker-compose.yml up -d postgres`) and expect FAIL (no `db` module).
- [ ] **Step 3: Write `docker-compose.yml`** (services `postgres` = `postgres:16`, ports `5433:5432`, env user/password `leads`, database `leads`, named volume, init dir mounted at `/docker-entrypoint-initdb.d`; `mailpit` = `axllent/mailpit`, ports `1025` SMTP and `8025` UI) and `01-create-test-db.sql` (`CREATE DATABASE leads_test;`).
- [ ] **Step 4: Write `0001_init.sql`** with the spec §5 columns, `CHECK` constraints from Global Constraints, indexes on `leads(created_at)`, `leads(status)`, `leads(lower(email), created_at)`, `replies(lead_id, status)`, and `replies.edited boolean NULL`. Implement `db.connect` and `db.migrate`; add the fixtures; add the config values and `.env.example` names.
- [ ] **Step 5: Run** `timeout 120 uv run pytest tests/db -q`. Expected: all pass.
- [ ] **Step 6: Commit** `feat: postgres + mailpit compose, schema migration, db fixtures`.

### Task 2: Leads repository

**Files:**
- Create: `lead_responder/records.py`, `lead_responder/repository.py`, `tests/db/test_repository_leads.py`

**Interfaces:**
- Consumes: `db.connect`, fixtures from Task 1, `models.Classification`, `router.Decision`.
- Produces: in `records.py`: `LeadStatus`, `ReplyStatus` (both `StrEnum`), frozen dataclasses `Lead(id, created_at, channel, name, email, phone, message, consent_at, category, risk, urgency, language, language_unsupported, ai_reason, needs_manual_review, status, anonymized_at)` and `Reply(id, lead_id, kind, ai_body, body, status, error, approved_by, sent_at, created_at, edited)`. In `repository.py`: `class Repository(conn)` with
  `create_lead_if_new(*, name: str, email: str, phone: str | None, message: str, consent_at: datetime, now: datetime, channel: str = "web_form") -> tuple[Lead, bool]` (bool = created; atomic via `pg_advisory_xact_lock` on a hash of lower(email) + stripped message, then duplicate lookup in the last 2 minutes, then insert with status `new`; returns the existing lead and `False` for a duplicate),
  `get_lead(lead_id: int) -> Lead | None`,
  `set_classification(lead_id: int, *, classification: Classification | None, decision: Decision) -> Lead` (stores category, risk, urgency, language, ai_reason from the classification when present; `language_unsupported` and `needs_manual_review` from the decision),
  `set_lead_status(lead_id: int, status: LeadStatus) -> Lead`.

- [ ] **Step 1: Write the failing tests** with exact assertions: `test_create_lead_returns_new_lead_with_status_new`; `test_duplicate_within_two_minutes_returns_existing_and_false` (same email in different case and message with extra whitespace, 119 s later); `test_same_message_after_three_minutes_is_a_new_lead`; `test_different_message_is_a_new_lead`; `test_two_connections_racing_create_exactly_one_lead` (two threads, each with its own connection, same payload, count rows == 1 and exactly one `True`); `test_nul_bytes_are_stripped` (`"a\x00b"` stored as `"ab"`); `test_sql_metacharacters_are_stored_literally` (`"Robert'); DROP TABLE leads;--"`, then the table still exists); `test_emoji_and_long_message_roundtrip` (10,000 characters of `"ä😀"` accepted; 10,001 raises `ValueError` before touching the database); `test_naive_datetimes_are_rejected` (`ValueError` for `now` and `consent_at`); `test_set_classification_with_none_classification_keeps_decision_flags`; `test_set_lead_status_rejects_unknown_status` (type error or `ValueError`, not a DB crash).
- [ ] **Step 2: Run** `timeout 120 uv run pytest tests/db/test_repository_leads.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `records.py` and the four methods above in `repository.py`. Normalise: strip NUL characters from all text fields; lower-case the email for duplicate comparison only (store as given); validate lengths (name 200, email 254, phone 50, message 10,000) and timezone awareness in Python so the user sees a clear `ValueError`; keep the `CHECK`s as the last defence. Use `conn.transaction()` so each method commits atomically.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: leads repository with atomic duplicate detection`.

### Task 3: Replies repository

**Files:**
- Modify: `lead_responder/repository.py`
- Create: `tests/db/test_repository_replies.py`

**Interfaces:**
- Consumes: `Repository`, `records.Reply`, `ReplyStatus`, `router.Action`.
- Produces: `add_reply(lead_id: int, *, kind: Action, ai_body: str | None, body: str, status: ReplyStatus, now: datetime, edited: bool | None = None) -> Reply`; `set_reply_status(reply_id: int, status: ReplyStatus, *, now: datetime, error: str | None = None, approved_by: int | None = None, edited: bool | None = None) -> Reply` (sets `sent_at = now` only when the new status is `sent`); `replies_for(lead_id: int) -> list[Reply]` ordered by `created_at, id`.

- [ ] **Step 1: Write the failing tests:** `test_add_reply_stores_ai_body_and_body_separately`; `test_replies_for_returns_in_creation_order`; `test_set_reply_status_sent_sets_sent_at`; `test_failed_reply_keeps_error_text` (`"timeout: may have been sent"`); `test_reply_for_missing_lead_raises` (`psycopg.errors.ForeignKeyViolation`); `test_status_and_kind_are_enums_not_free_text` (passing the string `"banana"` raises `ValueError`).
- [ ] **Step 2: Run** `timeout 120 uv run pytest tests/db/test_repository_replies.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the three methods; map rows to `Reply`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: replies repository`.

### Task 4: Retention (DSGVO)

**Files:**
- Create: `lead_responder/retention.py`, `lead_responder/scripts/run_retention.py`, `tests/db/test_retention.py`
- Modify: `lead_responder/repository.py`

**Interfaces:**
- Consumes: `Repository`, `config.RETENTION_DAYS`.
- Produces: `Repository.anonymize_older_than(*, cutoff: datetime, now: datetime) -> int` (for every lead with `created_at < cutoff` and `anonymized_at IS NULL`: `name = '[anonymized]'`, `email = ''`, `phone = NULL`, `message = ''`, `anonymized_at = now`; for their replies `body = '[anonymized]'` and `ai_body = NULL`; `category`, `risk`, `urgency`, `language`, statuses, timestamps and `edited` are kept; returns the number of leads changed); `retention.run(repo: Repository, *, days: int, now: datetime) -> int` (cutoff = `now - timedelta(days=days)`); script prints the count.

- [ ] **Step 1: Write the failing tests:** `test_personal_fields_are_blanked_and_statistics_kept`; `test_reply_texts_are_blanked_too` (name inside the greeting is gone, `edited` and `status` stay); `test_exact_boundary` (a lead exactly 90 days old stays, one second older is anonymized); `test_young_leads_are_untouched`; `test_second_run_changes_nothing` (returns 0, `anonymized_at` not updated); `test_audit_rows_are_kept` (an `audit_log` row referencing the lead survives).
- [ ] **Step 2: Run** `timeout 120 uv run pytest tests/db/test_retention.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** both functions in one transaction per call; the script connects with `config.DATABASE_URL` and runs `retention.run` with `now=datetime.now(timezone.utc)` and `config.RETENTION_DAYS`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: DSGVO retention job`.

### Task 5: Visibility, docs, wrap-up

**Files:**
- Modify: `.claude/hooks/precommit.py`, `.claude/hooks/test_hooks.py`, `track-a-dental/a1-lead-responder/README.md`, `track-a-dental/a1-lead-responder/REVIEW.md`, `devsetup/CATALOG.md`

**Interfaces:**
- Produces: the pre-commit hook prints pytest's final summary line (for example `278 passed, 12 skipped`) instead of only "tests passed", and a plain warning line when any test was skipped: `pre-commit: N tests were SKIPPED (database not running?)`.

- [ ] **Step 1: Write the failing test** in `test_hooks.py`: `test_precommit_summary_line_is_extracted` for a helper `summarize(output: str) -> tuple[str, int]` returning the last summary line and the skip count (cases: `"278 passed in 9s"` gives `("278 passed in 9s", 0)`; `"270 passed, 8 skipped in 9s"` gives `(..., 8)`).
- [ ] **Step 2: Run** `timeout 120 uv run pytest .claude/hooks -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `summarize` and use it in `precommit.py`; update README (new "Run" rows: `docker compose up -d postgres mailpit`, `uv run pytest tests/db`, `uv run python -m lead_responder.scripts.run_retention`), mark 4a in `REVIEW.md`, add a CATALOG note if the skip visibility helped.
- [ ] **Step 4: Run** `timeout 150 uv run pytest -q`. Expected: everything passes with Postgres up; with Postgres stopped, DB tests skip and the commit hook warns.
- [ ] **Step 5: Commit** `feat: 4a wrap-up (skip visibility, docs)`.

## Self-review
- **Spec coverage:** §5 tables and constraints (Task 1), duplicate rule §7 (Task 2), reply fields (Task 3), §6 retention (Task 4), failure-mode rows 1-4 (Tasks 1-4 tests). `staff_users` and `audit_log` exist in the schema but get methods in 4c, on purpose (YAGNI).
- **Types:** `Repository` method names match across tasks 2-4; `Action` is reused for reply kind; `LeadStatus`/`ReplyStatus` live in `records.py` only.
- **Not in this plan:** HTTP, AI calls, sending, auth, dashboard (4b-4e).
