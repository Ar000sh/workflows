# A1 4b Simplification Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the accidental complexity of layer 4b (system replies sharing `drafted` with staff drafts, a two-transaction "give up the claim", five-commit result writes) without changing what patients or staff experience.

**Architecture:** A new reply status `queued` plus a kind/status CHECK moves the "never send an unapproved draft" rule from service code into the data model. A one-statement re-claim replaces give-up-then-claim in the sweep. Two bigger repository methods (`store_pipeline_result`, `receive_lead`) each do one transaction, so the commit stays in the repository (Way B).

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Alembic, Postgres 16 (Docker, port 5433), pytest, FastAPI. Run everything with `uv run` from the repo root.

**Spec:** `docs/superpowers/specs/2026-10-25-a1-4b-simplification-refactor-design.md` (decisions D1-D9, failure modes F1-F17). Read it first. Parent: `docs/superpowers/specs/2026-10-24-a1-layer-4b-intake-and-sender-design.md`.

**Branch:** create `refactor/a1-4b-simplify` from `main` (`0e4f97c` or later). Paths below are relative to `track-a-dental/a1-lead-responder/` unless they start with `docs/`, `devsetup/` or `PROJECT.md`.

## Global Constraints
- Never use `git commit --no-verify`; `.githooks/pre-commit` runs the suite (limit 90 s; docs-only commits skip it). If the gate hits the limit, stop and report; do not bypass.
- Every commit leaves the whole suite green (`uv run pytest`, currently 544 passed in about 50 s). Postgres and Mailpit must be up: `docker compose -f track-a-dental/a1-lead-responder/docker-compose.yml up -d postgres mailpit`.
- No real Claude calls anywhere (tests use `shared.testing.FakeLLM`); do not run `/run-evals`.
- Never write regexes or backslashes through bash heredocs or `python -c`; use Write/Edit. Slow commands run under `timeout`.
- Never use `git stash`, `git reset --hard` or `git checkout -- <file>`; if blocked, stop and report instead of asking.
- Never write `.env` or key literals.
- Comments teach briefly (what and why, in the style of `repository.py`); never the history of a change. Every lock says which lock and why.
- Docs change with the code: grep the old term (list in Task 6) before calling a task done.
- Concurrency rule (CLAUDE.md): every new lock or conditional write gets a two-session race test that is shown to FAIL with the fix removed (comment the line out, run, expect red, restore, run, expect green). A change to locking needs the fresh-agent attack pass in Task 6.
- Lock order stays: lead row first, then replies (same as retention). The only new lock is the advisory lock on the mailbox key in `receive_lead`, taken first and alone.
- Final reply statuses: `drafted` `approved` `queued` `sending` `sent` `rejected` `failed`. `sent` and `rejected` stay final. Log lines carry ids and event names only.
- Implementers work from this plan only; if the plan and the code disagree, stop and report which line.

## Review Focus
1. A database at the OLD head holding system replies in `drafted` (and every other old status) must upgrade without error and with the system replies as `queued` (F1, Task 1).
2. A pipeline result that arrives after the 45 s fallback already acknowledged the lead must store NOTHING: no classification, no draft, no flag (F7, Task 3).
3. Ten simultaneous posts from one mailbox with limit 3 must give exactly 3 unsuppressed leads (F5, Task 4).
4. A reply whose lead lost its address after the claim ends `failed` with `error = None`, never stuck in `sending` (F17, Task 1).
5. A commit whose answer is lost (the write committed, the caller saw an error) must end with exactly one email: the fallback sees the first reply and returns False, and sweep 1b sends the `queued` reply (F9, Task 3).

---

## File Structure
| File | Change |
|---|---|
| `lead_responder/db/models.py` | `ReplyStatus.QUEUED`, new `REPLY_TRANSITIONS`, `ck_replies_kind_status` |
| `alembic/versions/<rev>_queued_status.py` | new migration (create with `uv run alembic -c track-a-dental/a1-lead-responder/alembic.ini revision -m "queued status"`, then edit) |
| `lead_responder/repository.py` | `AnonymizedLead`, `StoredResult`, `ReceivedLead`, `store_pipeline_result`, `receive_lead`, `reclaim_stuck_reply`, `stale_queued_replies`; delete `set_classification`, `suppress_lead`, `SUPPRESS_TRANSITIONS`, `fail_stuck_reply`, `failed_stuck_replies`, `stale_drafted_first_replies`, enum guards |
| `lead_responder/services/replies.py` | delete `SEND_FROM`, `_REFUSE_TO_FAILED`, `STUCK_ERROR`; add `_deliver`; simpler `_send` and `sweep` |
| `lead_responder/services/intake.py` | simpler `receive` and `process`; delete `_write_result`, `_known_anonymized`, `_anonymized` |
| `tests/test_status_transitions.py`, `tests/db/*` | see each task; new files `tests/db/test_store_pipeline_result.py`, `tests/db/test_receive_lead.py` |
| docs | Task 6 |

---

### Task 1: `queued` status, kind/status CHECK, simpler `send()` (spec 5.1, D1, D4; F1, F2, F13, F17)

**Files:**
- Modify: `lead_responder/db/models.py`, `lead_responder/repository.py` (`acknowledge_lead`, rename `stale_drafted_first_replies`), `lead_responder/services/replies.py` (`_send`, `_refuse`, `sweep`)
- Create: `alembic/versions/<rev>_queued_status.py`
- Test: `tests/test_status_transitions.py`, `tests/db/test_schema.py`, `tests/db/test_reply_service.py`, `tests/db/test_sweep.py`, `tests/db/test_repository_acknowledge.py`, `tests/db/test_repository_queries.py`, `tests/db/test_intake_service.py`, `tests/db/test_end_to_end.py`

**Interfaces:**
- Produces: `ReplyStatus.QUEUED = "queued"`; `REPLY_TRANSITIONS` exactly: `DRAFTED:{APPROVED,REJECTED}`, `QUEUED:{SENDING}`, `APPROVED:{SENDING}`, `SENDING:{SENT,FAILED}`, `FAILED:{SENDING}`, `SENT:{}`, `REJECTED:{}`; CHECK `ck_replies_kind_status` = `(kind = 'draft' AND status <> 'queued') OR (kind <> 'draft' AND status NOT IN ('drafted','approved','rejected'))`; `Repository.stale_queued_replies(*, created_before: datetime) -> list[Reply]` (replies with status `queued`, `created_at < created_before`, lead not anonymized; no kind filter); `acknowledge_lead` inserts its reply as `queued`.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Create the branch** `git checkout -b refactor/a1-4b-simplify` and run `uv run pytest -q` to record the baseline (544 passed) and its time in your report.
- [ ] **Step 2: Write the failing tests.**
  - `tests/test_status_transitions.py::test_reply_transitions_are_exactly_these`: replace the allowed set with the 7-status table above (7 x 7 loop); `test_every_status_has_an_entry...` already covers the new member.
  - `tests/db/test_schema.py::test_kind_status_check_rejects_illegal_pairs`: raw INSERT of (`draft`,`queued`), (`acknowledgment`,`drafted`), (`auto_reply`,`approved`), (`acknowledgment`,`rejected`) each raises `IntegrityError`; (`draft`,`drafted`), (`acknowledgment`,`queued`), (`acknowledgment`,`sent`) succeed.
  - Update `test_every_allowed_value_is_accepted_by_the_database` so it only inserts legal kind/status pairs.
  - `tests/db/test_schema.py::test_migration_queued_converts_system_drafted_replies` (use `at_revision(engine, alembic_cfg, <revision e93e6e744ccc>, body)`): at the OLD head insert one lead and replies (`acknowledgment`,`drafted`), (`auto_reply`,`drafted`), (`draft`,`drafted`), (`acknowledgment`,`sent`), (`draft`,`approved`), (`acknowledgment`,`failed`), (`draft`,`sending`); upgrade; assert the first two are `queued`, every other row keeps its status. `test_migration_queued_downgrade`: queued rows return to `drafted`, the new CHECK is gone, inserting `queued` raises `IntegrityError`.
  - `tests/db/test_reply_service.py::test_send_outcome_by_kind_and_status` (parametrized): for kind `draft` and each status except `sending`-in-flight: `drafted`->`REFUSED` (row unchanged), `approved`->`SENT`, `failed`->`SENT`, `sent`/`rejected`/`sending`->`ALREADY_HANDLED`; for `acknowledgment` and `auto_reply`: `queued`->`SENT`, `failed`->`SENT`, `sent`/`sending`->`ALREADY_HANDLED`. Plus `test_unapproved_draft_cannot_be_claimed`: `Repository.set_reply_status(<drafted draft>, SENDING, now=NOW)` raises `InvalidTransition` and the row stays `drafted`.
  - `tests/db/test_reply_service.py::test_lead_without_address_after_claim_ends_failed_without_error` (F17): reply `queued`, lead anonymized; `send` returns `REFUSED`, reply status `failed`, `error is None`. Adjust `test_anonymized_lead_is_refused_and_reply_failed_without_error_text` and `test_lead_anonymized_right_after_the_claim_is_not_sent` to the new single check. Keep `test_unapproved_draft_of_lead_without_address_stays_drafted` unchanged (a `drafted` draft is refused first).
  - `tests/db/test_repository_acknowledge.py`: every expectation of `"drafted"` for an acknowledgment or auto-reply becomes `"queued"` (lines ~68, ~314). F13: `test_a_suppressed_lead_never_gets_a_queued_reply` already covered by `test_acknowledge_lead_refuses_a_suppressed_lead`; keep it.
  - `tests/db/test_sweep.py` stale-drafted tests (`test_stale_drafted_acknowledgment_is_sent_once` ... `test_stale_drafted_reply_of_anonymized_lead_is_skipped`, lines ~310-389): create the reply as `QUEUED` and rename `drafted` to `queued` in names; `test_stale_staff_draft_is_never_sent` stays with a `drafted` draft (now excluded by status alone). `tests/db/test_repository_queries.py::test_stale_drafted_first_replies_filters_kind_status_age_and_anonymized` becomes `test_stale_queued_replies_filters_status_age_and_anonymized`.
  - Fix any other test that creates a system reply with `ReplyStatus.DRAFTED` (find with `grep -rn "DRAFTED\|\"drafted\"" tests`): system kinds use `QUEUED`; staff drafts keep `DRAFTED`.
- [ ] **Step 3: Run `uv run pytest tests/test_status_transitions.py tests/db/test_schema.py tests/db/test_reply_service.py -q`; expect FAIL** (no `QUEUED`).
- [ ] **Step 4: Implement.** Add the enum member, table and CHECK in `models.py` (a one-line why above the CHECK). Write the migration by hand: `down_revision = 'e93e6e744ccc'`; upgrade = drop `ck_replies_status`, recreate with `queued` added, `UPDATE replies SET status='queued' WHERE kind <> 'draft' AND status = 'drafted'`, create `ck_replies_kind_status`; downgrade reverses (drop kind/status CHECK, `queued`->`drafted`, restore the old status list; write the status lists out, do not import the models, as `e93e6e744ccc` does). In `repository.py` insert the first reply as `QUEUED` and rename/rewrite `stale_drafted_first_replies` to `stale_queued_replies` per Interfaces. In `replies.py`: delete `SEND_FROM` and `_REFUSE_TO_FAILED`; in `_send` replace step 2 with `status is DRAFTED -> REFUSED`, `status not in (QUEUED, APPROVED, FAILED) -> ALREADY_HANDLED`; delete the address check before the claim; keep the one after the claim and make `_refuse` move `sending -> failed` with `error=None`, returning `REFUSED`; the sweep's part 1b calls `stale_queued_replies` and sends each through `send`. Update the module docstring and the comments you touch.
- [ ] **Step 5: Run `uv run pytest -q`; expect all green** (fix remaining `drafted` expectations the full run reveals).
- [ ] **Step 6: Commit** `refactor(4b): queued status and kind/status CHECK replace the per-kind send table` (the gate runs the suite).

---

### Task 2: Re-claim stuck replies in place (spec 5.2, D3; F3)

**Files:**
- Modify: `lead_responder/repository.py`, `lead_responder/services/replies.py`
- Test: `tests/db/test_sweep.py`, `tests/db/test_repository_queries.py`, `tests/db/test_repository_replies.py`, `tests/db/test_reply_service.py`

**Interfaces:**
- Produces: `Repository.reclaim_stuck_reply(self, reply_id: int, *, claimed_before: datetime, now: datetime, max_attempts: int) -> Reply` (one conditional UPDATE: `status='sending' AND claimed_at < claimed_before AND send_attempts < max_attempts` sets `claimed_at=now`, `send_attempts=send_attempts+1`, returns the row; 0 rows raises `InvalidTransition`; requires timezone-aware datetimes). `ReplyService._deliver(self, reply_id: int, *, subject_language: str | None) -> SendOutcome`: everything after a successful claim (fresh read of reply and lead, address check, sender call with no open transaction, record, update lead); `send()` and the sweep both end in it.
- Consumes: Task 1's `send()` shape.

- [ ] **Step 1: Write the failing tests.**
  - `tests/db/test_repository_replies.py::test_reclaim_stuck_reply_moves_only_a_stale_claim_below_the_cap`: stuck reply -> returns the row with `status == "sending"`, fresh `claimed_at == now`, `send_attempts` +1; a young claim, a `sent` reply, and one at the cap each raise `InvalidTransition` and stay unchanged.
  - `tests/db/test_sweep.py::test_two_sweeps_reclaim_one_stuck_reply_once` (F3): two threads with a barrier call `ReplyService.sweep()` on one stuck acknowledgment; exactly one email in the fake sender, `send_attempts` increased by 1, final status `sent`. Show it FAILS with `Reply.claimed_at < claimed_before` removed from the UPDATE.
  - Adapt `test_stale_sweep_does_not_take_over_a_fresh_claim`, `test_stuck_acknowledgment_is_retried_after_timeout`, `test_acknowledgment_at_attempt_cap_is_left_for_staff`, `test_two_overlapping_sweeps_send_one_email_per_stuck_reply_and_per_lead` to the new path (no `STUCK_ERROR` text anywhere).
  - Delete: `test_fail_stuck_reply_moves_only_a_claim_older_than_the_cutoff` (replaced by the first test above), the five leftovers tests in `test_sweep.py` (`test_acknowledgment_left_failed_by_a_broken_sweep_is_retried_next_time`, `test_failed_with_stuck_error_is_resent`, `test_failed_with_other_error_or_at_cap_is_untouched`, `test_failed_with_stuck_error_but_young_claim_waits`, `test_two_overlapping_sweeps_resend_a_failed_by_sweep_acknowledgment_once`) and `test_failed_stuck_replies_filters_kind_error_age_and_attempts`. In your report list each deleted test and the test that now covers its behaviour.
- [ ] **Step 2: Run the new tests; expect FAIL** (`reclaim_stuck_reply` undefined).
- [ ] **Step 3: Implement.** Add `reclaim_stuck_reply` (same style as the removed `fail_stuck_reply`, comment says why the age check is in the same statement); delete `fail_stuck_reply`, `failed_stuck_replies`, `STUCK_ERROR`, the "failed by sweep" list and `_retry_stuck`'s second step. Split `_send` so the claim ends `_send` and `_deliver` does the rest; `_retry_stuck(reply_id, stuck_before)` becomes: `reclaim_stuck_reply` (`InvalidTransition` -> return False), then `self._attempted(self._deliver(reply_id, subject_language=None))`. `SweepResult` fields do not change. Update the `sweep()` comments (parts 1, 1-flag, 1b, 2).
- [ ] **Step 4: Run `uv run pytest -q`; expect all green.** Then do the red check of Step 1's race test and restore.
- [ ] **Step 5: Commit** `refactor(4b): re-claim a stuck reply in one statement; drop the failed-by-sweep path`.

---

### Task 3: `store_pipeline_result` and a simpler `process()` (spec 5.3, D2; F7, F8, F9, F13)

**Files:**
- Modify: `lead_responder/repository.py`, `lead_responder/services/intake.py`
- Create: `tests/db/test_store_pipeline_result.py`
- Test: `tests/db/test_intake_service.py`, `tests/db/test_repository_acknowledge.py`, `tests/db/test_retention.py`

**Interfaces:**
- Produces: `class AnonymizedLead(ValueError)` (raised by `_refuse_if_anonymized`, so every repository method refuses an anonymized lead with it); `@dataclass(frozen=True) class StoredResult: first_reply_id: int | None; draft_id: int | None`; `Repository.store_pipeline_result(self, lead_id: int, *, classification: Classification | None, decision: Decision, replies: Sequence[GeneratedReply], now: datetime) -> StoredResult` (`GeneratedReply` from `lead_responder.pipeline`, imported under `TYPE_CHECKING`; at most one first reply of kind `acknowledgment`/`auto_reply` and at most one `draft`, else `ValueError`). Behaviour, one transaction, one commit: lock the lead `FOR NO KEY UPDATE`; `AnonymizedLead` if anonymized; `InvalidTransition` unless the lead is `new`, not `send_suppressed` and has no first reply; store the classification fields and `language_unsupported`/`needs_manual_review`; move the status by chaining `_move_status` over `LEAD_TRANSITIONS` (`new->classified` when `classification is not None and not decision.language_unsupported`; `->acknowledged` when the first reply is an acknowledgment; `->awaiting_approval` when a draft exists); insert the first reply `queued` and the draft `drafted`. Any exception rolls back everything. Internals shared with `acknowledge_lead` go into a private `_insert_first_reply_locked`.
- Consumes: Task 1 (`queued`).
- Deleted: `Repository.set_classification`, `IntakeService._write_result`, `_known_anonymized`, `_anonymized`.

- [ ] **Step 1: Write the failing tests** in `tests/db/test_store_pipeline_result.py` (fixtures from `tests/db/conftest.py`; build `Decision`/`Classification` the way `tests/db/test_intake_service.py` does):
  - `test_sensitive_result_is_stored_whole`: lead `awaiting_approval`, category/risk/language stored, one `acknowledgment` `queued`, one `draft` `drafted`; returned ids match the rows.
  - `test_routine_auto_reply_result`: lead `classified`, one `auto_reply` `queued`, `draft_id is None`.
  - `test_classifier_failure_result` (classification `None`, ack only): lead `acknowledged`, `needs_manual_review`, category `None`. `test_unsupported_language_result`: `new -> acknowledged`, `language_unsupported`.
  - `test_failure_in_the_middle_stores_nothing`: monkeypatch the draft insert to raise; lead still `new`, category `None`, zero replies. Shown FAILING if the method commits between steps.
  - `test_late_result_loses_to_the_fallback` (F7): two threads, barrier; one calls `ReplyService.acknowledge_with_fallback`, the other `store_pipeline_result`; exactly one first reply exists; if the fallback won, the lead has no category and no draft exists. Run 20 rounds. Shown FAILING with the "no first reply yet" check removed.
  - `test_waits_for_a_retention_batch_then_raises_anonymized_lead` (F8): same shape as the existing `test_add_reply_during_a_retention_batch_waits_then_refuses`.
  - `test_refuses_a_suppressed_lead` (F13) and `test_refuses_a_lead_that_is_not_new`.
  - In `tests/db/test_intake_service.py` add `test_commit_ok_but_answer_lost_ends_with_one_email` (F9): wrap `store_pipeline_result` so it commits then raises; `process()` must run the fallback, which returns False and flags the lead; running `ReplyService.sweep()` afterwards (age the reply) sends the `queued` reply exactly once; exactly one email overall.
  - Delete `test_first_reply_is_still_sent_when_a_later_write_and_the_anonymized_check_fail` (the `finally` path no longer exists; F9 test replaces it). Rewrite `test_anonymized_lead_result_is_discarded_without_error` and `test_result_after_another_writer_acknowledged_is_discarded` to run through the new method. Move `test_set_classification_refused_after_status_moved` and `test_set_classification_waits_for_an_acknowledgment_then_refuses` into the new file against `store_pipeline_result` (names `..._refused_when_status_moved`, `..._waits_for_an_acknowledgment_then_refuses`).
- [ ] **Step 2: Run the new file; expect FAIL** (method undefined).
- [ ] **Step 3: Implement** `AnonymizedLead`, `StoredResult`, `store_pipeline_result` (comment block: the steps, the lock and why `FOR NO KEY UPDATE`, the table as single truth) and the shared private helper. In `intake.py`: `process` becomes: `result = _run_pipeline(...)`; `None` -> `_fallback` and return; else `try: stored = repo.store_pipeline_result(...)`, `except (InvalidTransition, AnonymizedLead)`: log "result discarded" and return; `except Exception`: log class name, `_fallback`, return; then `_send(lead_id, stored.first_reply_id)` when not `None`. `_send(self, lead_id: int, reply_id: int | None) -> None` keeps its flag-on-raise behaviour. Delete the dead methods.
- [ ] **Step 4: Run `uv run pytest -q`; expect all green;** do the red checks listed in Step 1 and restore.
- [ ] **Step 5: Commit** `refactor(4b): store the pipeline result in one transaction; process() loses its finally`.

---

### Task 4: `receive_lead` with a mailbox lock (spec 5.3; F5, F6, F10)

**Files:**
- Modify: `lead_responder/repository.py`, `lead_responder/services/intake.py`
- Create: `tests/db/test_receive_lead.py`
- Test: `tests/db/test_repository_acknowledge.py`, `tests/db/test_repository_queries.py`, `tests/db/test_intake_service.py`, `tests/db/test_api.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class ReceivedLead: lead: Lead; created: bool; suppressed: bool`; `Repository.receive_lead(self, *, name: str, email: str, phone: str | None, message: str, consent_at: datetime, now: datetime, mailbox_key: str, max_per_hour: int, max_per_day: int, channel: str = "web_form") -> ReceivedLead`. One transaction: `pg_advisory_xact_lock` on an id derived from the mailbox key (new `_mailbox_lock_id(mailbox_key)`, same hashing style as `_lock_id`); duplicate (same dedup key, last 2 minutes) -> `created=False`, nothing written; else insert, count the mailbox's leads in the last hour and day inside the same transaction (new private `_count_leads(*conditions)` WITHOUT commit; the public counts keep committing), and if `in_hour > max_per_hour or in_day > max_per_day` move `new -> acknowledged` over `LEAD_TRANSITIONS` with `send_suppressed=True, needs_manual_review=True`; one commit. `create_lead_if_new` is kept unchanged (shared private insert helper) for tests that only need a lead.
- Consumes: nothing new. Deleted: `Repository.suppress_lead`, `SUPPRESS_TRANSITIONS`.

- [ ] **Step 1: Write the failing tests** in `tests/db/test_receive_lead.py`:
  - `test_new_lead_is_saved_not_suppressed`; `test_identical_post_within_two_minutes_is_a_duplicate` (`created is False`, one row).
  - `test_over_the_hour_limit_suppresses_in_the_same_transaction`: limit 3, fourth call -> `suppressed`, status `acknowledged`, `send_suppressed`, `needs_manual_review`, no replies. Same for the day limit (`test_eleventh_in_a_day`).
  - `test_failure_after_the_insert_saves_nothing`: monkeypatch the suppress step to raise; zero lead rows.
  - `test_ten_simultaneous_posts_of_one_mailbox_count_exactly` (F5): 10 threads, barrier, different messages, `max_per_hour=3`; exactly 3 leads unsuppressed and 7 suppressed. Shown FAILING with the advisory lock line commented out.
  - `test_simultaneous_identical_posts_make_one_lead`: 2 and 10 threads, one row.
  - `test_receive_store_and_retention_together_do_not_deadlock` (F6): 30 rounds of threads running `receive_lead`, `store_pipeline_result` on an earlier lead, and `anonymize_older_than`; assert no exception.
  - Move `test_suppress_lead_flags_and_acknowledges`, `test_suppress_lead_refuses_a_classified_lead`, `test_suppress_lead_refuses_anonymized_and_unknown_leads` into this file as `receive_lead` cases or delete the ones that no longer apply (say which in your report). In `tests/db/test_intake_service.py` the limit tests (`test_fourth_lead_in_an_hour_is_suppressed_no_email_no_pipeline_call`, `test_eleventh_in_a_day`, `test_plus_alias_counts_toward_the_same_limit`) must stay and pass unchanged.
- [ ] **Step 2: Run the new file; expect FAIL.**
- [ ] **Step 3: Implement** `receive_lead` (comment: why the mailbox key and not the dedup key, why one lock covers both rules, why the lock is taken alone so no lock order exists to get wrong) and update `IntakeService.receive` to one call: duplicate -> `None`; else `Received(lead.id, run_pipeline=not suppressed)` with the same log lines as today. Delete `suppress_lead` and `SUPPRESS_TRANSITIONS`.
- [ ] **Step 4: Run `uv run pytest -q`; expect all green;** do the red check of the F5 test and restore.
- [ ] **Step 5: Commit** `refactor(4b): receive_lead saves, counts and suppresses in one transaction under one mailbox lock`.

---

### Task 5: Small cleanups and dead code (spec D6, D9)

**Files:** Modify `lead_responder/repository.py`, `lead_responder/services/replies.py`; Test: existing suites.

**Interfaces:** Produces: nothing new. Consumes: Tasks 1-4.

- [ ] **Step 1: Remove the enum `isinstance` guards** in `set_lead_status`, `add_reply`, `set_reply_status` and the sweep queries (a wrong value now reaches the database CHECK); keep `kind in FIRST_REPLY_KINDS` in `acknowledge_lead`. Delete or rewrite the tests that only asserted the guard's message (list them in your report).
- [ ] **Step 2: Build `SweepResult` with keyword arguments only** (`sweep()` and tests) and delete the "given by position" comment.
- [ ] **Step 3: Dead-code check:** for every public function and constant in `repository.py` and `services/*.py`, `grep -rn` its use in `lead_responder/` and `tests/`. Anything used by neither, or by tests only (other than `create_lead_if_new` and `add_reply`, kept on purpose), is deleted or reported.
- [ ] **Step 4: Run `uv run pytest -q`; expect all green;** record the suite time next to the Task 1 baseline.
- [ ] **Step 5: Commit** `refactor(4b): remove redundant enum guards and dead code`.

---

### Task 6: Docs, review file, attack pass (spec 6, 11, 13; F14, F15)

**Files:** Modify `docs/superpowers/specs/2026-10-05-a1-lead-responder-design.md` (sections 5, 7, 14), `docs/superpowers/specs/2026-10-24-a1-layer-4b-intake-and-sender-design.md`, `track-a-dental/a1-lead-responder/review/status-flows.md`, `review/4b-intake-sender.md`, `README.md`, `PROJECT.md`, `devsetup/CATALOG.md` (only if something was added). Create `review/refactor-simplification.md`, and add a row to `REVIEW.md`.

**Interfaces:** none.

- [ ] **Step 1: Grep for stale terms** and fix every hit in docs and comments: `drafted` (as the status of acknowledgments/auto-replies), `STUCK_ERROR`, `stuck: retried by sweep`, `fail_stuck_reply`, `failed_stuck`, `SEND_FROM`, `_REFUSE_TO_FAILED`, `suppress_lead`, `set_classification`, `_write_result`, `stale_drafted`, `_known_anonymized`, "give up the claim". The grep output must be empty (or only historical rows of the Decisions log).
- [ ] **Step 2: Update the diagrams and tables:** `status-flows.md` (reply machine with `queued`, the 4-part sweep, the path of one post), the method table and rulings 1, 7, 9 in `review/4b-intake-sender.md` (mark superseded, with the reason), the parked items that disappeared (crash between save and suppress; sweep leftovers; cap+1; anonymized `drafted` row becomes a `queued` row).
- [ ] **Step 3: Write `review/refactor-simplification.md`:** overview first (what changed, before/after table of removed code and tests, a table of the new public methods), then Tariq's review checklist, the decisions D1-D9 with who decided, and a Parked list (F4 late original sender; D7; D8). Add the row to `REVIEW.md`.
- [ ] **Step 4: PROJECT.md:** Decisions log rows for D1 (`queued`), D2 (Way B: commit stays in the repository, a few bigger methods), D3 (re-claim in place), D4 (address check after the claim only); update the status block.
- [ ] **Step 5: Measure F15:** run `uv run pytest -q` three times; report the times against the 90 s gate. Do not change the gate.
- [ ] **Step 6: Commit the docs** (`docs(4b): ...`), then STOP and report to the main session. **The attack pass is run by the main session, not by you:** `/edge-case-probe` in attack mode, a fresh agent, concurrency named as the focus (locks changed). Triage, fix the top 1-3 test-first, park the rest in `review/refactor-simplification.md`.

---

## Self-review notes
- Spec coverage: D1-D4, D6, D9 are Tasks 1-5; D5 (keep the auto-reply flag) is "no change" in Task 2's sweep comments; D7, D8 are out of scope (spec section 14). F1 T1, F2 T1, F3 T2, F4 parked, F5 T4, F6 T4, F7 T3, F8 T3, F9 T3, F10 covered by `test_identical_post_within_two_minutes_is_a_duplicate`, F11 unchanged (existing API tests), F12 parked, F13 T1/T3, F14 T6, F15 T6, F16 unchanged, F17 T1.
- Type names used across tasks: `ReplyStatus.QUEUED`, `stale_queued_replies`, `reclaim_stuck_reply`, `_deliver`, `AnonymizedLead`, `StoredResult`, `store_pipeline_result`, `ReceivedLead`, `receive_lead`, `_mailbox_lock_id`, `_count_leads`.
- Order matters: Task 1 is the only schema change; Tasks 2-4 touch different methods and may be reviewed independently; Task 3 depends on Task 1's `queued`.
