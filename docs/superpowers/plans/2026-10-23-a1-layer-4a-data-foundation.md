# A1 Layer 4a: Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Postgres (and Mailpit) in Docker, the full schema from the spec as SQLAlchemy models with Alembic migrations, and a tested repository layer for leads and replies, including duplicate detection and DSGVO retention. Nothing in 4a is reachable from the internet and nothing calls the AI.

**Architecture:** SQLAlchemy 2.0 typed ORM models (`Mapped[...]`) in `lead_responder/db/`, schema changes only through Alembic migrations, and a synchronous `Repository` class over one `Session` (FastAPI endpoints in 4b are plain `def`, so sync is enough and easy to read). The pipeline from layers 1-3 stays untouched; 4b will connect it to this repository.

**Tech Stack:** Python 3.12 (uv), SQLAlchemy >= 2.0, Alembic >= 1.13, `psycopg[binary]` >= 3.2 (driver), Postgres 16 and Mailpit via Docker Compose, pytest.

**Spec:** `docs/superpowers/specs/2026-10-05-a1-lead-responder-design.md` (§5 data model, §6 DSGVO, §7 duplicates, §14 failure modes rows 1-4). Parts 4b-4e get their own plans.

## Global Constraints
- Code in the existing package `track-a-dental/a1-lead-responder/lead_responder/` (new subpackage `db/`); tests in `.../a1-lead-responder/tests/` (DB tests in `tests/db/`). Compose file at `track-a-dental/a1-lead-responder/docker-compose.yml`; Alembic config at `track-a-dental/a1-lead-responder/alembic.ini` with scripts in `.../alembic/`.
- Postgres published on **host port 5433** (5432 is often taken on Windows). Test database `leads_test`, dev database `leads`, user and password `leads`. URLs use the driver prefix `postgresql+psycopg://`.
- Tables and columns exactly as spec §5 (`leads`, `replies`, `staff_users`, `audit_log`), plus two additions: `replies.edited boolean NULL` and `leads.dedup_key text NULL` (sha256 hex of lower-cased email + NUL + stripped message). All timestamps `timestamptz`; code accepts **timezone-aware datetimes only** (naive raises `ValueError`).
- Status values: lead `new|classified|auto_replied|acknowledged|awaiting_approval|replied|done`; reply `drafted|approved|sent|rejected|failed`; reply kind `auto_reply|acknowledgment|draft` (same strings as `router.Action`); audit action `approve|edit|reject|resend|login|logout`. Stored as `String` columns with explicit `CheckConstraint`s in `__table_args__`, so the database enforces them too.
- `RETENTION_DAYS` default **90**. Duplicate window **2 minutes**.
- The schema changes only through Alembic revisions; models and migrations must agree (a test checks it). No SQL built by string formatting of user data; the only raw SQL allowed is `text()` with bound parameters (the advisory lock).
- Secrets only in `.env`; `.env.example` lists every variable name. Claude never edits `.env`.
- Never write regexes or backslashes through bash heredocs; use Write/Edit. Slow commands under `timeout`. Every commit message ends with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. The pre-commit gate runs the suite; never `--no-verify`.
- Short teaching comments above each function (what it does and why), as in `pipeline.py`.
- Choices for Tariq's review: ORM + Alembic (Tariq's call); DB tests **skip** (not fail) when Postgres is not running; retention also blanks reply texts and the `dedup_key` hash (spec names only lead fields; both contain personal data).

## Review Focus
1. Two identical submissions at the same moment: exactly one lead is created (advisory lock test with two sessions).
2. Hostile text: NUL byte, `Robert'); DROP TABLE leads;--`, emoji, 10,000-character message; stored literally or rejected with a clear error, never an unhandled DB crash.
3. Retention: reply texts and `dedup_key` blanked too, statistics kept, idempotent, exact 90-day boundary, young leads untouched.
4. Naive datetimes and unknown status strings rejected before or by the database.
5. Models drifting from migrations (someone edits a model and forgets the migration): a test fails.
6. Postgres not running: tests skip with a message naming the fix, and the pre-commit gate prints the skip count (Task 5).

---

### Task 1: Infrastructure, models and first migration

**Files:**
- Create: `track-a-dental/a1-lead-responder/docker-compose.yml`, `.../docker/initdb/01-create-test-db.sql`, `.../alembic.ini`, `.../alembic/env.py`, `.../alembic/versions/<generated>_init.py`, `lead_responder/db/__init__.py`, `lead_responder/db/models.py`, `lead_responder/db/session.py`, `tests/db/conftest.py`, `tests/db/test_schema.py`
- Modify: `pyproject.toml` (add `sqlalchemy>=2.0`, `alembic>=1.13`, `psycopg[binary]>=3.2`), `lead_responder/config.py` (add `DATABASE_URL`, `DATABASE_URL_TEST`, `RETENTION_DAYS`), `.env.example`

**Interfaces:**
- Produces:
  - `db/models.py`: `class Base(DeclarativeBase)`; `Lead`, `Reply`, `StaffUser`, `AuditLog` (typed `Mapped[...]` columns named exactly as in Global Constraints); `LeadStatus`, `ReplyStatus` (`StrEnum`) with the values listed above.
  - `db/session.py`: `make_engine(url: str) -> Engine` (`pool_pre_ping=True`); `make_session_factory(engine: Engine) -> sessionmaker[Session]` (`expire_on_commit=False`).
  - `tests/db/conftest.py`: fixture `engine` (session scope; if `DATABASE_URL_TEST` is unreachable within 2 seconds it calls `pytest.skip("Postgres not reachable at <url>: run 'docker compose up -d postgres' in track-a-dental/a1-lead-responder")`; otherwise drops all tables and runs `alembic upgrade head` programmatically once); fixture `session` (new `Session` per test; before each test `TRUNCATE leads, replies, staff_users, audit_log RESTART IDENTITY CASCADE`; closed afterwards).

- [ ] **Step 1: Write the failing tests** in `tests/db/test_schema.py`: `test_upgrade_creates_all_tables` (the four spec tables plus `alembic_version` exist); `test_models_and_migration_agree` (`alembic.autogenerate.compare_metadata` between a migration context on the live DB and `Base.metadata` returns an empty list); `test_check_constraints_reject_unknown_values` (inserting a lead with status `"banana"`, a reply with kind or status `"banana"`, an audit row with action `"banana"` each raises `sqlalchemy.exc.IntegrityError` caused by `CheckViolation`); `test_lead_requires_consent_and_limits_lengths` (NULL `consent_at` raises `IntegrityError`; `name` of 201 characters and `message` of 10,001 characters raise `IntegrityError`).
- [ ] **Step 2: Run** `timeout 120 uv run pytest tests/db -q` with Postgres NOT started. Expected: all SKIPPED with the message above (proves the skip path). Then start Postgres (`docker compose -f track-a-dental/a1-lead-responder/docker-compose.yml up -d postgres`) and run again. Expected: FAIL (no models yet).
- [ ] **Step 3: Write the Compose file** (`postgres:16` on `5433:5432`, user/password/db `leads`, named volume, the initdb folder mounted at `/docker-entrypoint-initdb.d`; `axllent/mailpit` on `1025` SMTP and `8025` UI) and `01-create-test-db.sql` (`CREATE DATABASE leads_test;`).
- [ ] **Step 4: Write the models** with the columns, indexes (`leads(created_at)`, `leads(status)`, `leads(dedup_key, created_at)`, `replies(lead_id, status)`), `CheckConstraint`s and length limits (name 200, email 254, phone 50, message 10,000). Configure Alembic (`env.py` reads `config.DATABASE_URL`, uses `Base.metadata`), generate the first revision with `alembic revision --autogenerate -m init`, and **read the generated file** before keeping it (autogenerate misses `CheckConstraint`s on some versions; add them by hand if so). Add the session helpers, config values and `.env.example` names.
- [ ] **Step 5: Run** `timeout 120 uv run pytest tests/db -q`. Expected: all pass.
- [ ] **Step 6: Commit** `feat: postgres + mailpit compose, SQLAlchemy models, first alembic migration`.

### Task 2: Leads repository

**Files:**
- Create: `lead_responder/repository.py`, `tests/db/test_repository_leads.py`

**Interfaces:**
- Consumes: `Session` fixture and models from Task 1, `models.Classification` (pydantic, existing in `lead_responder/models.py`), `router.Decision`.
- Produces: `class Repository(session: Session)` with
  `create_lead_if_new(*, name: str, email: str, phone: str | None, message: str, consent_at: datetime, now: datetime, channel: str = "web_form") -> tuple[Lead, bool]` (atomic: inside one transaction take `pg_advisory_xact_lock` keyed by the first 8 bytes of the `dedup_key`, look for a lead with the same `dedup_key` and `created_at >= now - 2 minutes`, return it with `False` if found, otherwise insert with status `new` and return it with `True`),
  `get_lead(lead_id: int) -> Lead | None`,
  `set_classification(lead_id: int, *, classification: Classification | None, decision: Decision) -> Lead` (category, risk, urgency, language, ai_reason from the classification when present; `language_unsupported` and `needs_manual_review` always from the decision),
  `set_lead_status(lead_id: int, status: LeadStatus) -> Lead`.

- [ ] **Step 1: Write the failing tests** with exact assertions: `test_create_lead_returns_new_lead_with_status_new`; `test_duplicate_within_two_minutes_returns_existing_and_false` (same email in different case, message with extra whitespace, 119 s later); `test_same_message_after_three_minutes_is_a_new_lead`; `test_different_message_is_a_new_lead`; `test_two_sessions_racing_create_exactly_one_lead` (two threads, each with its own `Session` from the same engine, same payload; afterwards the row count is 1 and exactly one call returned `True`); `test_nul_bytes_are_stripped` (`"a\x00b"` stored as `"ab"`); `test_sql_metacharacters_are_stored_literally` (`"Robert'); DROP TABLE leads;--"`, table still exists and value roundtrips); `test_emoji_and_long_message_roundtrip` (10,000 characters of `"ä😀"` accepted; 10,001 raises `ValueError` before touching the database); `test_naive_datetimes_are_rejected` (`ValueError` for `now` and for `consent_at`); `test_set_classification_with_none_keeps_decision_flags`; `test_set_lead_status_rejects_a_plain_string` (`ValueError`, not a database error).
- [ ] **Step 2: Run** `timeout 120 uv run pytest tests/db/test_repository_leads.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** the four methods. Strip NUL characters from all text fields; compute `dedup_key` in Python (lower-cased email, NUL separator (chr(0)), stripped message, sha256 hex; computed after cleaning, changed from newline after the attack probe found a collision); validate lengths and timezone awareness in Python so the caller gets a clear `ValueError` (the `CHECK`s stay as the last defence); each public method commits one transaction.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: leads repository with atomic duplicate detection`.

### Task 3: Replies repository

**Files:**
- Modify: `lead_responder/repository.py`
- Create: `tests/db/test_repository_replies.py`

**Interfaces:**
- Consumes: `Repository`, models `Reply`, `ReplyStatus`, `router.Action`.
- Produces: `add_reply(lead_id: int, *, kind: Action, ai_body: str | None, body: str, status: ReplyStatus, now: datetime, edited: bool | None = None) -> Reply`; `set_reply_status(reply_id: int, status: ReplyStatus, *, now: datetime, error: str | None = None, approved_by: int | None = None, edited: bool | None = None) -> Reply` (sets `sent_at = now` only when the new status is `sent`); `replies_for(lead_id: int) -> list[Reply]` ordered by `created_at, id`.

- [ ] **Step 1: Write the failing tests:** `test_add_reply_stores_ai_body_and_body_separately`; `test_replies_for_returns_in_creation_order`; `test_set_reply_status_sent_sets_sent_at`; `test_failed_reply_keeps_error_text` (`"timeout: may have been sent"`); `test_reply_for_missing_lead_raises` (`IntegrityError` from the foreign key); `test_kind_and_status_must_be_enum_members` (the string `"banana"` raises `ValueError`).
- [ ] **Step 2: Run** `timeout 120 uv run pytest tests/db/test_repository_replies.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the three methods.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: replies repository`.

### Task 4: Retention (DSGVO)

**Files:**
- Create: `lead_responder/retention.py`, `lead_responder/scripts/run_retention.py`, `tests/db/test_retention.py`
- Modify: `lead_responder/repository.py`

**Interfaces:**
- Consumes: `Repository`, `config.RETENTION_DAYS`.
- Produces: `Repository.anonymize_older_than(*, cutoff: datetime, now: datetime) -> int` (for every lead with `created_at < cutoff` and `anonymized_at IS NULL`: `name = '[anonymized]'`, `email = ''`, `phone = NULL`, `message = ''`, `dedup_key = NULL`, `anonymized_at = now`; for their replies `body = '[anonymized]'` and `ai_body = NULL`; `category`, `risk`, `urgency`, `language`, statuses, timestamps and `edited` are kept; returns the number of leads changed; batched, see the fix wave: `batch_size`, one transaction per batch, plus a sweep of leftovers on already anonymized leads; ORM `update()` statements); `retention.run(repo: Repository, *, days: int, now: datetime) -> int` (cutoff = `now - timedelta(days=days)`); the script prints the count using `config.DATABASE_URL` and `config.RETENTION_DAYS`.

- [ ] **Step 1: Write the failing tests:** `test_personal_fields_are_blanked_and_statistics_kept`; `test_reply_texts_and_dedup_key_are_blanked_too`; `test_exact_boundary` (a lead exactly 90 days old stays, one second older is anonymized); `test_young_leads_are_untouched`; `test_second_run_changes_nothing` (returns 0, `anonymized_at` unchanged); `test_audit_rows_are_kept` (an `audit_log` row referencing the lead survives).
- [ ] **Step 2: Run** `timeout 120 uv run pytest tests/db/test_retention.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** both functions and the script (`now=datetime.now(timezone.utc)`).
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: DSGVO retention job`.

### Task 5: Visibility, docs, wrap-up

**Files:**
- Modify: `.claude/hooks/precommit.py`, `.claude/hooks/test_hooks.py`, `track-a-dental/a1-lead-responder/README.md`, `track-a-dental/a1-lead-responder/REVIEW.md`, `devsetup/CATALOG.md`

**Interfaces:**
- Produces: in `precommit.py`, `summarize(output: str) -> tuple[str, int]` returning pytest's last summary line and the number skipped; the hook prints that line instead of only "tests passed", and a warning `pre-commit: N tests were SKIPPED (database not running?)` when the count is above zero.

- [ ] **Step 1: Write the failing test** in `test_hooks.py`: `test_precommit_summary_line_is_extracted` (`"278 passed in 9s"` gives `("278 passed in 9s", 0)`; `"270 passed, 8 skipped in 9s"` gives `("270 passed, 8 skipped in 9s", 8)`).
- [ ] **Step 2: Run** `timeout 120 uv run pytest .claude/hooks -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `summarize` and use it. Update the README Run table (`docker compose up -d postgres mailpit`, `uv run alembic upgrade head`, `uv run pytest tests/db`, `uv run python -m lead_responder.scripts.run_retention`), tick 4a in `REVIEW.md`, and add the Alembic and ORM decision to `PROJECT.md`'s Decisions log.
- [ ] **Step 4: Run** `timeout 150 uv run pytest -q`. Expected: everything passes with Postgres up; with Postgres stopped, DB tests skip and the commit hook warns.
- [ ] **Step 5: Commit** `feat: 4a wrap-up (skip visibility, docs)`.

## Self-review
- **Spec coverage:** §5 tables and constraints (Task 1), duplicate rule §7 (Task 2), reply fields (Task 3), §6 retention (Task 4), failure-mode rows 1-4 (tests in Tasks 1-4). `staff_users` and `audit_log` exist in the schema but get repository methods in 4c, on purpose (YAGNI).
- **Types:** `Repository` method names match across tasks 2-4; `Action` is reused for reply kind; `LeadStatus` and `ReplyStatus` live in `db/models.py` only; the pydantic `Classification` stays in `lead_responder/models.py`, so the two `models` modules must not be confused (hence `db/models.py`).
- **Not in this plan:** HTTP, AI calls, sending, auth, dashboard (4b-4e).
