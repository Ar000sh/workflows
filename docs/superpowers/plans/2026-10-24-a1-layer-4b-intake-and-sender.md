# A1 Layer 4b: Intake API and Sender Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A website form posts a lead to a FastAPI endpoint; the lead is saved, run through the layer 1-3 pipeline, and the patient gets an acknowledgment or auto-reply by email (Mailpit in development) with claim-before-send, a sweep job for stuck sends, and abuse limits.

**Architecture:** API validates input (pydantic `LeadIn`) -> `IntakeService` (business rules, deadline, limits) -> `Repository` (database access, its own commit) -> `ReplyService` (claim, send, record) -> `Sender` (`SmtpSender`). A new reply status `sending` is the claim; `sending` means "outcome unknown", `failed` means "known not sent". A scheduled `run_sweep.py` retries stuck acknowledgments and answers leads that got no reply. The pipeline (`process_inquiry`) is unchanged.

**Tech Stack:** Python 3.12 (uv), FastAPI, pydantic v2 (`email-validator`), SQLAlchemy 2 + Alembic + psycopg (existing), `smtplib`, pytest, Postgres 16 + Mailpit in Docker (existing).

**Spec:** `docs/superpowers/specs/2026-10-24-a1-layer-4b-intake-and-sender-design.md` (read it first; section 4 holds every decision, section 8 the failure modes P1-P12). Parent spec: `docs/superpowers/specs/2026-10-05-a1-lead-responder-design.md` (§5, §7, §14 rows 5-11). Read A1 `REVIEW.md` "Carry into the 4b/4c/4d plans".

## Global Constraints
- Code in `track-a-dental/a1-lead-responder/lead_responder/` (new subpackages `api/`, `services/`), tests in `.../a1-lead-responder/tests/` (DB tests in `tests/db/`, they skip without Postgres). New dependencies in the root `pyproject.toml`: `fastapi>=0.115`, `email-validator>=2.1`, `uvicorn>=0.30`; dev group `httpx>=0.27` (TestClient). Python 3.12 pinned in `.python-version`.
- **Layering:** `api/` validates input only; `services/` holds business rules and never builds SQL; `repository.py` is database access, **one public method = one transaction, commits itself**; the database enforces hard limits. A rule lives in one layer unless the spec says why (spec §5 table).
- **Final status values.** Lead: `new|classified|auto_replied|acknowledged|awaiting_approval|replied|done` (unchanged). Reply: `drafted|approved|sending|sent|rejected|failed`. `REPLY_TRANSITIONS` after 4b: `drafted -> {approved, rejected, sending, failed}`; `approved -> {sending, failed}`; `sending -> {sent, failed}`; `failed -> {sending}`; `sent` and `rejected` final. `LEAD_TRANSITIONS` unchanged.
- `sending` = outcome unknown (timeout or crash); `failed` = known not sent. Only a claim (`-> sending`) may precede a send, and it commits **before** the send. **No database transaction or row lock stays open across an AI call or an SMTP call.**
- **Config defaults** (env-overridable, in `config.py`, names listed in `.env.example`; Claude never edits `.env`): `MAX_BODY_BYTES=32768`, `MAX_LEADS_PER_EMAIL_HOUR=3`, `MAX_LEADS_PER_EMAIL_DAY=10`, `MAX_AI_LEADS_PER_HOUR=60`, `MAX_CONCURRENT_PIPELINES=10`, `PIPELINE_DEADLINE_SECONDS=45`, `SENDING_TIMEOUT_MINUTES=10`, `MAX_SEND_ATTEMPTS=3`, `NO_REPLY_SWEEP_MINUTES=5`, `SMTP_HOST=localhost`, `SMTP_PORT=1025`, `SMTP_STARTTLS=false`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_TIMEOUT_SECONDS=10`, `MAIL_FROM` (empty = practice email), `CORS_ORIGINS` (comma list, default empty).
- Send retry policy (parent spec §7): refused/unreachable provider -> up to 3 retries, waits 1 s, 2 s, 4 s, then `FAILED`; a timeout -> `TIMEOUT` at once, never retried by the sender. Per-email limit: **more than 3 per hour or more than 10 per day** counted on the normalized mailbox key, **including the new lead itself**.
- Lead limits (single definition in `limits.py`, used by the API model and the `CheckConstraint`s in `db/models.py`): name 200, email 254, phone 50, message 10,000 characters. The AI limit `config.MAX_MESSAGE_CHARS=5000` is unchanged (longer messages are stored, not sent to the AI; existing route-1 behaviour).
- Text from the AI or from staff (classification `reason`/`language`, reply bodies, send errors) keeps the repository's `_clean`/length-cut: it is not form input and has no API in front of it in 4b. Only **lead form fields** move out of the repository.
- Logs carry ids and event names only: never email, name, phone, message or reply text, not even inside logged exception text. A send `error` stores the exception class name only, never the recipient address.
- Never write regexes or backslashes through bash heredocs; use Write/Edit and prove each regex with a test. Slow commands under `timeout`. Every commit message ends with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. The pre-commit gate runs the suite (docs-only commits skip it, 90 s limit); **never `--no-verify`**. If the gate hits the limit or you are blocked: **stop and report, do not ask**.
- Short teaching comments above each function (what, why, what flows where), as in `pipeline.py`; no change history in comments. Every new lock, conditional write and column gets a one-line why. Docs change with code in the same commit (README, spec, comments); grep for the old term.
- A new lock or conditional write gets a **two-session race test, shown failing with the fix removed** (note the red run in the commit message).

## Review Focus
1. Two senders (two workers, a double click) on one reply: exactly one email (Task 4 `test_two_sends_of_one_reply_at_once_send_one_email`).
2. Two writers for a lead's first acknowledgment (deadline fallback, late pipeline, two sweeps): one acknowledgment, no lead left "acknowledged" without a reply (Task 3 race and atomicity tests).
3. A hostile form post: 500 MB body, whitespace-only message, newline in email, NUL, lone surrogate, 255-character email, `+tag` and Gmail-dot aliases: stored cleanly or rejected with 4xx, never a 500 with internals (Tasks 1 and 7).
4. A reply stuck in `sending`: acknowledgment retried at most `MAX_SEND_ATTEMPTS`, draft never retried automatically (Task 5).
5. Personal data in logs and in send errors (Task 8 `test_no_personal_data_in_logs`). An emoji-heavy 10,000-character message can exceed `MAX_BODY_BYTES` and gets `413`; accepted.

---

### Task 1: Shared limits, `LeadIn`, mailbox key; lead validation leaves the repository

**Files:**
- Create: `lead_responder/limits.py`, `lead_responder/api/__init__.py`, `lead_responder/api/schemas.py`, `tests/test_limits.py`, `tests/test_schemas.py`
- Modify: root `pyproject.toml` (deps above), `lead_responder/db/models.py` (CHECK strings built from `limits`), `lead_responder/repository.py` (remove `MAX_NAME/MAX_EMAIL/MAX_PHONE/MAX_MESSAGE`, `_check_len`, and the `_clean` calls on lead form fields in `create_lead_if_new`), `tests/db/test_repository_leads.py` (move/rewrite the tests named below), `.env.example`

**Interfaces:**
- Produces: `limits.py`: `MAX_NAME=200`, `MAX_EMAIL=254`, `MAX_PHONE=50`, `MAX_MESSAGE=10_000`; `clean_text(value: str) -> str` (removes NUL); `mailbox_key(email: str) -> str` (lower-case; local part cut at the first `+`; dots removed from the local part only for `gmail.com` and `googlemail.com`; the domain `googlemail.com` maps to `gmail.com`).
- Produces: `api/schemas.py`: `class LeadIn(BaseModel)` with `name: str`, `email: EmailStr`, `phone: str | None = None`, `message: str`, `consent: bool`; after validation `name`, `email`, `phone`, `message` are NUL-stripped and trimmed; `consent` must be `True`.

- [ ] **Step 1: Write the failing tests.** `test_limits.py`: `test_mailbox_key_strips_plus_tag_and_case` (`"Victim+1@Example.com"` and `"victim+2@example.com"` give the same key `"victim@example.com"`); `test_mailbox_key_removes_dots_only_for_gmail` (`"v.i.c@gmail.com"` -> `"vic@gmail.com"`, `"v.i.c@example.com"` unchanged, `"v.i.c+x@googlemail.com"` -> `"vic@gmail.com"`); `test_clean_text_removes_nul`. `test_schemas.py` (pydantic `ValidationError` unless stated): missing consent and `consent=False`; invalid email; email with a newline (`"a@b.de\nBcc: x@y.de"`); 255-character email; name of 201 and message of 10,001 characters; whitespace-only name and whitespace-only message; message `"a\x00b"` becomes `"ab"`; message of exactly 10,000 and name of exactly 200 accepted; phone of 51 characters; a lone surrogate (`"\ud800"`) rejected; emoji and umlauts accepted.
- [ ] **Step 2: Run** `timeout 120 uv run pytest tests/test_limits.py tests/test_schemas.py -q`. Expected: FAIL (modules missing).
- [ ] **Step 3: Implement** `limits.py` and `LeadIn` (field constraints first so size checks run before validators; whitespace-only rejected after trimming). `db/models.py` builds its four length CHECK strings from `limits` (same constraint names, same values, so no migration is needed).
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Remove the lead-field rules from the repository** and port the tests, none deleted without an equivalent: in `tests/db/test_repository_leads.py` rewrite `test_nul_bytes_are_stripped` (repository now raises `sqlalchemy.exc.DBAPIError` for NUL; the stripping assertion lives in `test_schemas.py`), `test_overlong_fields_raise_value_error` (repository raises `sqlalchemy.exc.IntegrityError` from the CHECK and the session stays usable; the `ValueError` assertions live in `test_schemas.py`), and the 10,001 part of `test_emoji_and_long_message_roundtrip`. `_require_aware` stays. Comment the `dedup_key` helper: callers must pass `LeadIn`-cleaned text.
- [ ] **Step 6: Run the full suite** `timeout 300 uv run pytest -q` (Postgres up). Expected: PASS, including `tests/test_attack_findings.py` and `tests/db/` regressions; port any that break to the API layer.
- [ ] **Step 7: Commit** `feat(4b): shared limits, LeadIn request model, mailbox key; lead validation moves out of the repository`.

### Task 2: Schema for `sending` (statuses, columns, migration, claim)

**Files:**
- Create: `alembic/versions/<generated>_sending_claim.py`, `tests/test_reply_transitions.py` (or extend `tests/test_status_transitions.py`)
- Modify: `lead_responder/db/models.py`, `lead_responder/repository.py` (`set_reply_status`), `lead_responder/retention.py`/`anonymize_older_than`, `tests/test_status_transitions.py`, `tests/db/test_schema.py`, `tests/db/test_repository_replies.py`, `tests/db/test_retention.py`

**Interfaces:**
- Produces: `ReplyStatus.SENDING = "sending"`; the new `REPLY_TRANSITIONS` (Global Constraints); `Reply.claimed_at: datetime | None` (timestamptz), `Reply.send_attempts: int` (`NOT NULL`, default 0, `CHECK >= 0`); `Lead.send_suppressed: bool` (`NOT NULL`, default false); `Lead.mailbox_key: str | None` (Text, index `ix_leads_mailbox_key_created_at` on (`mailbox_key`, `created_at`), blanked by retention, personal-data derived like `dedup_key`).
- Produces: `Repository.set_reply_status(reply_id, ReplyStatus.SENDING, now=now)` is **the claim**: it also sets `claimed_at = now` and `send_attempts = send_attempts + 1` in the same conditional UPDATE; returns the reply; raises `InvalidTransition` for the loser.

- [ ] **Step 1: Write the failing tests.** Transition table test with every pair (allowed set exactly as in Global Constraints; `sent` and `rejected` final; `failed -> sent` and `failed -> failed` now refused). `test_claim_sets_claimed_at_and_counts_attempts` (claim, `sending -> failed`, claim again: `send_attempts == 2`). `test_two_sessions_claiming_one_reply_exactly_one_wins` (two threads, own `Session` each, from `drafted`; one returns, the other raises `InvalidTransition`; row ends `sending` with `send_attempts == 1`). `test_stale_session_cannot_claim_a_sent_reply`. Migration tests: `test_migration_upgrade_keeps_existing_rows` (upgrade to the `init` revision, insert a lead and replies in every old status, upgrade to head: rows intact, `send_attempts == 0`, `send_suppressed` false); `test_migration_downgrade_maps_sending_to_failed` (insert a `sending` reply, downgrade one revision: it becomes `failed`, columns gone, status CHECK back to the old list); `test_models_and_migration_agree` still passes. `test_retention_blanks_mailbox_key`.
- [ ] **Step 2: Run** `timeout 120 uv run pytest tests/test_status_transitions.py tests/db -q`. Expected: FAIL.
- [ ] **Step 3: Implement** models and tables (one-line why per new column and index, e.g. `claimed_at`: "when the current send attempt started, so the sweep can find stuck sends"). Generate the revision with `alembic revision --autogenerate -m "sending claim"`, **read it** and fix by hand: the status CHECK must be dropped and recreated, the downgrade first updates `sending` rows to `failed`. `set_reply_status` adds the two claim values when the target is `SENDING`. Retention also sets `mailbox_key=None`.
- [ ] **Step 4: Run** the same command. Expected: PASS. Then remove the `status IN` condition from `_move_status` temporarily and confirm `test_two_sessions_claiming_one_reply_exactly_one_wins` FAILS; restore it.
- [ ] **Step 5: Fix older tests** that relied on `drafted -> sent`, `failed -> sent` or `failed -> failed` (`tests/db/test_repository_replies.py`, `tests/test_status_transitions.py`, `tests/test_attack_findings.py`); each gets the equivalent through `sending`. Run `timeout 300 uv run pytest -q`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(4b): reply status sending as the claim, claimed_at/send_attempts/send_suppressed/mailbox_key, migration`.

### Task 3: Repository methods for acknowledgment, conditional classification, limits and sweep queries

**Files:**
- Modify: `lead_responder/repository.py`, `tests/db/test_repository_leads.py`, `tests/db/test_repository_replies.py`
- Create: `tests/db/test_repository_acknowledge.py`, `tests/db/test_repository_queries.py`

**Interfaces:**
- Consumes: Task 2 columns and tables.
- Produces (all on `Repository`, one transaction each, all refuse an anonymized lead with `ValueError` except where noted):
  - `create_lead_if_new(*, name, email, phone, message, consent_at, now, mailbox_key: str, channel="web_form") -> tuple[Lead, bool]` (stores `mailbox_key`).
  - `acknowledge_lead(lead_id: int, *, kind: Action, ai_body: str | None, body: str, now: datetime, needs_manual_review: bool = False) -> Reply`. `kind` must be `ACKNOWLEDGMENT` or `AUTO_REPLY`. Under `FOR NO KEY UPDATE` on the lead row: raise `InvalidTransition` if the lead already has a reply of either kind, or its status is not `new`, `classified` or `acknowledged`; for `ACKNOWLEDGMENT` move `new`/`classified` -> `acknowledged`; insert the reply as `drafted`; set `needs_manual_review` when asked; all in one transaction.
  - `set_classification(...)`: same signature, now also raises `InvalidTransition` unless the lead's status is `new` (checked under the existing lock).
  - `suppress_lead(lead_id: int) -> Lead`: sets `send_suppressed = true`, `needs_manual_review = true`, moves `new -> acknowledged` (`InvalidTransition` otherwise).
  - `flag_for_review(lead_id: int) -> Lead`: sets `needs_manual_review = true`, no status change; allowed on anonymized leads.
  - `get_reply(reply_id: int) -> Reply | None` (reads like `get_lead`).
  - `count_leads_for_mailbox_since(mailbox_key: str, since: datetime) -> int`; `count_leads_since(since: datetime) -> int`.
  - `stuck_sending_replies(*, kind: Action, claimed_before: datetime, max_attempts: int) -> list[Reply]` (status `sending`, `claimed_at < claimed_before`, `send_attempts < max_attempts`).
  - `leads_without_first_reply(*, created_before: datetime) -> list[Lead]` (status in `new|classified|acknowledged`, `send_suppressed` false, not anonymized, older than the cutoff, no reply of kind `acknowledgment` or `auto_reply`).

- [ ] **Step 1: Write the failing tests.** `test_acknowledge_lead_moves_status_and_inserts_reply_in_one_transaction` (sensitive lead `classified` -> `acknowledged`, one `drafted` reply); `test_acknowledge_lead_failure_leaves_status_unchanged` (force the insert to fail, e.g. `lead_id` deleted via monkeypatch of `session.add`; lead status still `new`: **atomicity**); `test_two_sessions_acknowledging_one_lead_exactly_one_wins` (two threads; one reply row, the other `InvalidTransition`); `test_acknowledge_lead_on_acknowledged_lead_without_reply_works_once` (sweep case: status stays `acknowledged`); `test_auto_reply_kind_does_not_move_status`; `test_set_classification_refused_after_status_moved` (`InvalidTransition`) and still works on a `new` lead; `test_suppress_lead_flags_and_acknowledges`; `test_flag_for_review_keeps_status`; `test_count_includes_the_new_lead_itself` and `test_two_simultaneous_leads_for_one_mailbox_both_count_two` (two threads, both then count 2); `test_counts_respect_since_boundary` (exactly at `since` counted or not: pick `>=` and assert it); `test_stuck_sending_replies_filters_kind_age_and_attempts`; `test_leads_without_first_reply_skips_suppressed_replied_and_young_leads`.
- [ ] **Step 2: Run** `timeout 120 uv run pytest tests/db -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the methods. `acknowledge_lead` reuses `_move_status` for the lead move and `_anonymized_under_lock(..., will_update_lead=True)` for the lock; the count queries use the new index. Comment above `acknowledge_lead` why: "the status move and the reply insert must be one step, or a crash leaves a lead that looks handled with no reply".
- [ ] **Step 4: Run** the same command. Expected: PASS. Then disable the "already has a first reply" check and confirm `test_two_sessions_acknowledging_one_lead_exactly_one_wins` FAILS; restore.
- [ ] **Step 5: Update callers/tests** of `create_lead_if_new` (new `mailbox_key` argument) and `set_classification` (leads must be `new`); run `timeout 300 uv run pytest -q`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(4b): acknowledge_lead, conditional set_classification, suppress/flag, limit and sweep queries`.

### Task 4: Sender and `ReplyService.send`

**Files:**
- Create: `lead_responder/sender.py`, `lead_responder/services/__init__.py`, `lead_responder/services/replies.py`, `tests/test_sender.py`, `tests/db/test_reply_service.py`
- Modify: `lead_responder/config.py`, `lead_responder/templates.py` (add `subject(language: str, practice: Practice) -> str`: fixed text per language, `de+en` and unknown give the bilingual subject), `.env.example`, `tests/test_templates.py`

**Interfaces:**
- Produces: `sender.py`: `class Outcome(StrEnum)`: `ACCEPTED`, `FAILED`, `TIMEOUT`; `@dataclass(frozen=True) class SendResult(outcome: Outcome, detail: str)` (`detail` = exception class name or empty, never an address); `class Sender(Protocol)`: `send(*, to: str, subject: str, body: str) -> SendResult`; `class SmtpSender` with `__init__(*, host, port, from_addr, reply_to, starttls=False, user=None, password=None, timeout=10.0, sleep=time.sleep)`.
- Produces: `services/replies.py`: `class SendOutcome(StrEnum)`: `SENT`, `FAILED`, `IN_DOUBT`, `ALREADY_HANDLED`, `REFUSED`; `class ReplyService.__init__(*, session_factory, sender: Sender, practice: Practice, clock: Callable[[], datetime])`; `send(reply_id: int) -> SendOutcome`; `acknowledge_with_fallback(lead_id: int) -> bool` (builds the `de+en` acknowledgment with `emergency=True` and `practice.is_open(now)`, name through `sanitize_name`, calls `acknowledge_lead(kind=ACKNOWLEDGMENT, needs_manual_review=True)` then `send`; returns `False` if another writer already acknowledged).

- [ ] **Step 1: Write the failing tests.** `test_sender.py` with a fake `smtplib.SMTP` class monkeypatched in: `test_accepted_mail_returns_accepted_and_builds_plain_text_message` (From, To, Reply-To, fixed Subject, UTF-8 body); `test_refused_connection_is_retried_three_times_with_1_2_4_second_waits_then_failed` (fake `sleep` records `[1, 2, 4]`, four attempts in total); `test_timeout_is_never_retried_and_returns_timeout`; `test_auth_error_and_5xx_are_retried_then_failed`; `test_recipient_with_newline_is_refused_before_connecting` (`FAILED`, no connection); `test_detail_never_contains_the_recipient_address`. `test_reply_service.py` (fake `Sender` recording calls and checking `session.in_transaction()`): `test_sent_reply_is_recorded_sent_with_sent_at`; `test_two_sends_of_one_reply_at_once_send_one_email` (two threads; exactly one `ACCEPTED` call, other outcome `ALREADY_HANDLED`); `test_sender_runs_with_no_open_transaction`; `test_provider_failure_ends_failed_with_class_name_only`; `test_timeout_leaves_reply_in_sending_and_returns_in_doubt`; `test_recording_failure_after_accepted_leaves_sending` (the repository call after the send raises; reply stays `sending`); `test_anonymized_lead_is_refused_and_reply_failed_without_error_text` (`REFUSED`); `test_draft_must_be_approved_before_send` (a `drafted` draft is refused with `ALREADY_HANDLED`/`REFUSED`, no email; an `approved` draft sends); `test_auto_reply_sent_moves_lead_to_auto_replied`; `test_auto_reply_failed_or_in_doubt_flags_lead_and_moves_it_to_acknowledged`; `test_failed_acknowledgment_flags_lead`; `test_resend_of_failed_reply_claims_again` (`failed -> sending`, attempts 2); `test_acknowledge_with_fallback_sends_once_when_called_twice` (two threads; one `True`, one `False`, one email).
- [ ] **Step 2: Run** `timeout 120 uv run pytest tests/test_sender.py tests/db/test_reply_service.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `SmtpSender` (one `smtplib.SMTP` per attempt with `timeout`; `TimeoutError`/`socket.timeout` -> `TIMEOUT` immediately; other `OSError`/`SMTPException` -> retry; the retry policy lives here, spec §7). Implement `ReplyService.send`: (1) read reply and lead with `Repository` (commits), refuse anonymized/blank email via `set_reply_status(FAILED, error=None)`; allowed kinds start states: `acknowledgment`/`auto_reply` from `drafted` or `failed`, `draft` only from `approved` or `failed`; (2) claim with `set_reply_status(SENDING, now=clock())`, `InvalidTransition` -> `ALREADY_HANDLED`; (3) call `sender.send` with the session idle; (4) record `SENT` or `FAILED(error=detail)`; on `TIMEOUT` record nothing; (5) apply the lead-status rule for `auto_reply`. Add config values and `.env.example` names.
- [ ] **Step 4: Run** the same command. Expected: PASS. Remove the claim (send without `set_reply_status(SENDING)`) and confirm `test_two_sends_of_one_reply_at_once_send_one_email` FAILS; restore.
- [ ] **Step 5: Commit** `feat(4b): SmtpSender with typed results and ReplyService.send with claim before send`.

### Task 5: Sweep and `run_sweep.py`

**Files:**
- Modify: `lead_responder/services/replies.py`
- Create: `lead_responder/scripts/run_sweep.py`, `tests/db/test_sweep.py`

**Interfaces:**
- Consumes: Task 3 queries, Task 4 `send` and `acknowledge_with_fallback`.
- Produces: `ReplyService.sweep() -> SweepResult` where `SweepResult` is a frozen dataclass `(retried_acknowledgments: int, fallback_acknowledgments: int, left_for_staff: int)`; `scripts/run_sweep.py` `main() -> int` shaped like `run_retention.py` (uses `make_session_factory`, prints the counts).

- [ ] **Step 1: Write the failing tests.** `test_stuck_acknowledgment_is_retried_after_timeout` (claimed 11 minutes ago, attempts 1: ends `sent`, attempts 2); `test_acknowledgment_at_attempt_cap_is_left_for_staff` (attempts 3: untouched, counted in `left_for_staff`); `test_stuck_draft_is_never_touched` and `test_stuck_auto_reply_is_never_touched` (stay `sending`); `test_young_sending_reply_is_not_touched` (claimed 9 minutes ago; exactly 10 minutes: pick and assert the boundary); `test_lead_without_reply_gets_fallback_acknowledgment` (6 minutes old, `new`, flagged, one reply, one email); `test_acknowledged_lead_with_lost_acknowledgment_gets_one` ; `test_suppressed_lead_is_skipped`; `test_two_overlapping_sweeps_send_one_email_per_stuck_reply_and_per_lead` (two threads); `test_sweep_twice_is_idempotent`; `test_sweep_skips_anonymized_leads`.
- [ ] **Step 2: Run** `timeout 120 uv run pytest tests/db/test_sweep.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `sweep`: part 1 for acknowledgments only: `sending -> failed` then `send` (the loser of a race gets `InvalidTransition`, skipped); part 2 over `leads_without_first_reply(created_before=now - NO_REPLY_SWEEP_MINUTES)` using `acknowledge_with_fallback`. Add `run_sweep.py`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(4b): sweep for stuck acknowledgments and leads with no reply`.

### Task 6: `IntakeService` (limits, pipeline under a deadline, writing results)

**Files:**
- Create: `lead_responder/services/intake.py`, `tests/db/test_intake_service.py`
- Modify: `lead_responder/config.py`

**Interfaces:**
- Consumes: `LeadIn`, `Repository`, `process_inquiry` and `PipelineResult` (unchanged), `ReplyService`, `limits.mailbox_key`.
- Produces: `class IntakeService.__init__(*, session_factory, replies: ReplyService, llm: LLMClient, practice: Practice, faq: str, clock: Callable[[], datetime], pipeline=process_inquiry)`; `receive(lead: LeadIn) -> int | None` (saves via `create_lead_if_new`; a duplicate returns `None`; then applies the limits: over the per-email limit `suppress_lead` and stop; over `MAX_AI_LEADS_PER_HOUR` or no free pipeline slot -> mark for fallback; returns the lead id, and tells the caller whether to run `process(lead_id)`; builder chooses the return shape, e.g. a small dataclass `Received(lead_id, run_pipeline: bool)`); `process(lead_id: int) -> None` (never raises).

- [ ] **Step 1: Write the failing tests** with a fake pipeline (callable returning a hand-built `PipelineResult`) and fake sender: `test_routine_lead_gets_auto_reply_and_becomes_auto_replied`; `test_sensitive_lead_gets_acknowledgment_and_draft_and_awaits_approval` (reply kinds, statuses `sent` / `drafted`, lead `awaiting_approval`); `test_classifier_failure_route_sends_acknowledgment_only_and_flags` (lead `acknowledged`, `needs_manual_review`); `test_unsupported_language_route_sends_combined_acknowledgment_no_draft`; `test_duplicate_returns_none_and_sends_nothing`; `test_fourth_lead_in_an_hour_is_suppressed_no_email_no_pipeline_call` and `test_eleventh_in_a_day`; `test_plus_alias_counts_toward_the_same_limit`; `test_global_ai_cap_sends_fixed_acknowledgment_without_pipeline_call`; `test_no_free_pipeline_slot_sends_fixed_acknowledgment_only` (semaphore of size 1 held by a blocked fake pipeline); `test_pipeline_over_deadline_sends_fallback_and_late_result_writes_nothing` (deadline 0.2 s, fake pipeline sleeps 0.6 s: one acknowledgment total, lead not classified by the late result, no second email); `test_any_pipeline_exception_sends_fallback_and_flags`; `test_orphaned_run_keeps_its_pipeline_slot_until_it_finishes`; `test_database_error_on_save_raises_and_sends_nothing`; `test_anonymized_lead_result_is_discarded_without_error`; `test_no_session_is_in_a_transaction_while_the_pipeline_runs`.
- [ ] **Step 2: Run** `timeout 120 uv run pytest tests/db/test_intake_service.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Write order per result: `set_classification` (conditional) -> `set_lead_status(CLASSIFIED)` only when a classification exists and the language is supported -> for each generated reply: `AUTO_REPLY`/`ACKNOWLEDGMENT` through `acknowledge_lead`, `DRAFT` through `add_reply(status=DRAFTED)` -> lead `awaiting_approval` when a draft was added -> `ReplyService.send` for each acknowledgment/auto-reply. Any `InvalidTransition` while writing means someone else won: stop and discard. Run the pipeline in a `ThreadPoolExecutor` future with `future.result(timeout=PIPELINE_DEADLINE_SECONDS)`; a `BoundedSemaphore(MAX_CONCURRENT_PIPELINES)` acquired non-blocking before starting and released in the future's done-callback (an orphan keeps its slot). Draft guardrail warnings are not stored in 4b (4d recomputes them at approval; note this in `REVIEW.md`). Comments say why for each of: deadline, orphan, slot, discard.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(4b): IntakeService with rate limits, pipeline deadline, concurrency cap and result writing`.

### Task 7: API

**Files:**
- Create: `lead_responder/api/app.py`, `lead_responder/api/main.py`, `tests/db/test_api.py`
- Modify: `lead_responder/config.py`, `.env.example`

**Interfaces:**
- Consumes: `LeadIn`, `IntakeService`.
- Produces: `create_app(*, intake: IntakeService, max_body_bytes: int = config.MAX_BODY_BYTES, cors_origins: list[str] = ...) -> FastAPI` with `POST /leads` (`202` and `{"status": "received"}` for new and duplicate leads alike; the pipeline runs as a background task) and `GET /health`; `api/main.py` builds `app` from config for `uvicorn lead_responder.api.main:app`.

- [ ] **Step 1: Write the failing tests** (`TestClient`, real test database, fake sender and pipeline): `test_post_valid_lead_returns_202_and_stores_it`; `test_duplicate_post_returns_same_answer_and_stores_one_lead`; `test_missing_consent_returns_422_and_stores_nothing`; `test_newline_in_email_returns_422`; `test_body_over_limit_returns_413_before_parsing_and_stores_nothing` (declared `Content-Length` larger than the limit, and a chunked body that grows past it); `test_non_json_body_returns_4xx`; `test_database_down_returns_503_with_generic_text_and_sends_nothing` (session factory pointed at a closed port); `test_unexpected_error_returns_500_without_internals` (no traceback, no table or column names, no submitted email in the body); `test_response_does_not_reveal_whether_a_lead_was_a_duplicate_or_limited`; `test_cors_only_allows_configured_origins`; `test_health_returns_ok`.
- [ ] **Step 2: Run** `timeout 120 uv run pytest tests/db/test_api.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Body limit as ASGI middleware (check `Content-Length`, and count bytes while streaming); one session per request through `make_session_factory`; `POST /leads` is a plain `def`; generic exception handlers.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(4b): FastAPI intake endpoint with body-size limit and generic errors`.

### Task 8: Logs, end-to-end, docs

**Files:**
- Create: `tests/db/test_logging.py`, `tests/db/test_end_to_end.py`
- Modify: `lead_responder/pipeline.py` and any other log call the test finds leaking, `README.md`, `REVIEW.md`, `docs/superpowers/specs/2026-10-05-a1-lead-responder-design.md` (§5 status flow and reply statuses, §7 timeout row, §14 row 11 wording), `docs/superpowers/specs/2026-10-24-a1-layer-4b-intake-and-sender-design.md` (statuses), `PROJECT.md` (status, decisions log row), `devsetup/CATALOG.md` if anything was added

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the failing tests.** `test_no_personal_data_in_logs` (post leads whose email, name, phone and message contain canary strings through the whole flow including a failing LLM, a failing sender and a guardrail rejection; `caplog` at DEBUG contains none of the canaries, in messages or exception text); `test_send_error_text_holds_no_address`. End-to-end (skip when Mailpit on `localhost:8025` is unreachable, with the fix hint in the skip text; fake LLM, real `SmtpSender`): `test_routine_lead_reaches_mailpit`; `test_sensitive_lead_gets_acknowledgment_in_mailpit_and_a_draft_row`; `test_turkish_language_lead_gets_combined_acknowledgment_and_no_draft`; `test_duplicate_post_sends_nothing_more`; `test_hostile_form_posts` (500 KB body, whitespace message, `+tag` aliases beyond the limit: 4xx or suppressed, the Mailpit count stays within `MAX_LEADS_PER_EMAIL_HOUR`).
- [ ] **Step 2: Run** `timeout 180 uv run pytest tests/db/test_logging.py tests/db/test_end_to_end.py -q`. Expected: FAIL (leaks found).
- [ ] **Step 3: Fix** every leaking log call (ids and event names only). Run again. Expected: PASS.
- [ ] **Step 4: Docs in the same commit.** README: how to run (`docker compose up -d postgres mailpit`, `alembic upgrade head`, `uvicorn lead_responder.api.main:app`, a `curl` example, `python -m lead_responder.scripts.run_sweep`, scheduler note). Amend the parent spec: a timeout leaves the reply in `sending` (not `failed`), the acknowledgment retry rule, new statuses and the status flow. Then run `grep -rn "timeout: may have been sent\|no automatic retry\|may be resent or fail again\|failed -> sent" docs track-a-dental PROJECT.md` and fix every hit that is now wrong. `REVIEW.md`: a 4b section that starts with an overview (what was built, how the parts fit, a table of every public method of `Repository`, `ReplyService`, `IntakeService`, `SmtpSender`), then the checklist for Tariq, the carry-over for 4c/4d (staff resolve/resend actions, draft warnings not stored, scheduler for sweep and retention), and the parked items already listed there. `PROJECT.md`: current status and one decisions-log row for the 4b decisions (claim by `sending`, stuck policy by kind, plain functions plus a sweep job).
- [ ] **Step 5: Run the whole suite** `timeout 300 uv run pytest -q` and report the pass and skip counts. Expected: PASS.
- [ ] **Step 6: Commit** `feat(4b): log hygiene, end-to-end tests, docs and review guide`.

---

## After the last task (main session, not part of the tasks)
Run `/edge-case-probe` in **attack mode** with concurrency named as the focus (fresh agent, spec and public interfaces only). Triage, fix the top 1-3 test-first, park the rest in `REVIEW.md`. Run `/run-evals` only if a prompt, router or guardrail changed (none planned). Finish with `/retro` at the end of layer 4, not now.
