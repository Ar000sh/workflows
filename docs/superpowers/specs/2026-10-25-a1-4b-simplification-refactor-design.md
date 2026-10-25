# A1 4b simplification refactor: Design Spec

> Part of the A1 lead responder. Follows the merged layer 4b (`0e4f97c`) and comes BEFORE 4c (auth and audit) and 4d (dashboard). Parent specs: `2026-10-05-a1-lead-responder-design.md` (sections 5, 7, 14) and `2026-10-24-a1-layer-4b-intake-and-sender-design.md`. Input: an independent Opus design review of 2026-10-25 (read-only, fresh agent) and Tariq's question "do we have too many checks and edge cases because of the status design?". Status: **draft, waiting for Tariq's decisions in section 4 and the plan.**

## 1. Purpose and business outcome
Not a new feature: the same behaviour with less code and fewer special cases. It serves outcome 1 ("never miss a lead") and keeps its promises: no email twice, an accepted email never recorded as failed, no patient left without a reply, no personal data in logs, DSGVO retention.

Why now: 4c adds approve, edit, reject and resend actions with audit rows, and 4d adds the dashboard, both on top of the status model. Two weaknesses make every one of those actions need extra checks, and each patch so far produced its own edge cases (ruling 7, ruling 9's `finally`, the sweep "leftovers" list, two parked bugs). Fixing them after 4d costs about three times as much (review estimate). Learning goal: telling inherent complexity (an email cannot be taken back) from accidental complexity (a data model that lets illegal states exist).

## 2. Context and constraints
- Volume: a few dozen leads a day; one developer; must be explainable to a small-business client.
- Fake data only. Tests need Postgres (5433) and Mailpit; the pre-commit gate has a 90 s limit and ran 43-60 s (once 91 s); this refactor should make the suite smaller, not larger.
- Alembic migrations only; the dev database has fake data, but the migration still has an upgrade and a downgrade that are tested.
- No real Claude calls are needed (all tests use `FakeLLM`). `/run-evals` is NOT needed: no prompt, model, router or guardrail changes.

## 3. Autonomy decision
Workflow, unchanged: our code decides every step. This refactor touches no AI behaviour.

## 4. Decisions
Tariq decides the rows marked **Tariq**. Rows marked Claude are my calls, with the reason, and can be overruled.

| # | Decision | Choice | Why | Who |
|---|---|---|---|---|
| D1 | How to tell a system reply (acknowledgment, auto-reply) from a staff draft | New reply status **`queued`** = "a system reply, ready to send, needs no approval". `drafted` then means only "a staff draft waiting for approval". | The status table itself then says what may be sent, so the service no longer re-checks it (`SEND_FROM`, ruling 7, `_REFUSE_TO_FAILED`, the kind filter in sweep 1b go). Alternative: reuse `approved` with no approver ("approved by policy"): no new status, but `approved` would mean two things. | **Tariq** (recommended: `queued`) |
| D2 | Atomic writes without reversing "the commit stays in the repository" ("Way B") | A few bigger repository methods, each ONE transaction: `store_pipeline_result` and `receive_lead`. Every other method keeps its own commit. | Keeps Tariq's 2026-10-24 rule ("one method = one transaction") and removes the half-written-result paths. `acknowledge_lead` already works this way. The unit-of-work alternative would touch every repository method and test. | Tariq (decided in conversation 2026-10-25) |
| D3 | Giving up a stuck claim | **Re-claim in place**: one UPDATE keeps the status `sending`, sets a fresh `claimed_at`, adds 1 to `send_attempts`, and only matches `sending`, older than the cutoff, below the attempt cap. The sweep then delivers with the re-claim as its claim. `fail_stuck_reply`, `failed_stuck_replies`, `STUCK_ERROR` and the "leftovers" list are deleted. | The two-transaction give-up-then-claim created the crash window that needed the leftovers list. One statement has no window. | Claude |
| D4 | Address check | Only AFTER the claim (the check that closes probe 11f). The check before the claim and the `drafted/approved -> failed` refusal moves go. | A lead with no address then costs one claim count and ends `failed` with no error text. Simpler table, same safety. | Claude |
| D5 | Sweep flagging of stuck auto-replies | **Keep** until 4d lists replies stuck in `sending`. | The review called it redundant because `_update_lead` flags on `IN_DOUBT`. That is only true when the process lives: a crash after the claim never reaches `_update_lead`, so the sweep flag is the only signal. I checked this in `replies.py`. | Claude (overrules the review on this point) |
| D6 | Repository `isinstance` guards on enums | Remove those that duplicate a database CHECK or a type hint (`set_lead_status`, `add_reply`, `set_reply_status`, the three sweep queries). Keep `kind in FIRST_REPLY_KINDS` in `acknowledge_lead` (it is a business rule, not a typo guard). | A wrong enum reaches the CHECK anyway; the guards only improve the message. | Claude |
| D7 | Lead status model (`acknowledged` is overloaded; `needs_manual_review`, `send_suppressed`, `language_unsupported` carry the difference) | **Not in this refactor.** Decide in the 4d spec, when the dashboard says which lead states staff need. | Highest blast radius (review Option 3); the cost today is about 40 lines. | Tariq, later |
| D8 | A `failed` acknowledgment (refused 3 times, known not sent) is not retried by the sweep | **Unchanged**, parked. It waits for staff (4d "resend"). Possible later: let the sweep claim `failed` acknowledgments below the attempt cap. | Out of scope: behaviour, not simplification. Today the sweep never retried these either. | Tariq, later |
| D9 | Retention batching, `MAX_CONCURRENT_PIPELINES` semaphore, `SweepResult` positional defaults | Retention and the semaphore stay. `SweepResult` is built with keyword arguments only and the "by position" comment goes. | Retention is built and tested; the semaphore is 10 lines and bounds orphans. | Claude |

## 5. Architecture and components
Layering is unchanged (API validates, service holds business rules, repository is database access with its own commit, the database enforces hard limits). What changes is the contents.

### 5.1 Reply statuses (Part 1, decision D1)
```
drafted ──approve──► approved ──claim──► sending ──► sent (final)
   └─reject─► rejected (final)               ▲  └──► failed ──claim again──► sending
queued ───────────────claim─────────────────┘
```
New `REPLY_TRANSITIONS`:
| From | May move to |
|---|---|
| `drafted` (staff drafts only) | `approved`, `rejected` |
| `queued` (system replies only) | `sending` |
| `approved` | `sending` |
| `sending` | `sent`, `failed` |
| `failed` | `sending` |
| `sent`, `rejected` | none (final) |

Removed moves: `drafted -> sending`, `drafted -> failed`, `approved -> failed`.

A database CHECK `ck_replies_kind_status` ties kind and status together, so the combinations that matter cannot exist even through a bug: a `draft` is never `queued`; an `acknowledgment` or `auto_reply` is never `drafted`, `approved` or `rejected`.
```
(kind = 'draft' AND status <> 'queued')
OR (kind <> 'draft' AND status NOT IN ('drafted', 'approved', 'rejected'))
```
Result: a staff draft can reach `sending` only through `approved`, by construction. `send()` keeps a one-line read (`status == drafted` -> `REFUSED`, ruling 1's outcome) but no table of allowed statuses per kind.

`acknowledge_lead` inserts the reply as `queued` (was `drafted`). `add_reply` stays for staff drafts and tests.

### 5.2 Sending and the sweep (Part 2, decisions D3, D4, D5)
`ReplyService.send(reply_id)` = read, `drafted` check, **claim**, address check (after the claim only), then the shared private `_deliver`: sender call with no transaction open, `_record`, `_update_lead`. A new `Repository.reclaim_stuck_reply(reply_id, *, claimed_before, now, max_attempts)` is the sweep's claim: it replaces `fail_stuck_reply`; the sweep then calls `_deliver` directly (the status is already `sending`, so `send()` would answer `ALREADY_HANDLED`).

```
UPDATE replies SET claimed_at = :now, send_attempts = send_attempts + 1
WHERE id = :id AND status = 'sending' AND claimed_at < :cutoff AND send_attempts < :cap
RETURNING *        -- 0 rows: someone else handled it (InvalidTransition), skip
```
Sweep after the refactor:
| Part | Finds | Does |
|---|---|---|
| 1 | acknowledgments in `sending` older than `SENDING_TIMEOUT_MINUTES` | re-claim in place and deliver; at `MAX_SEND_ATTEMPTS` they are counted as `left_for_staff` |
| 1-flag | unflagged leads whose auto-reply is stuck in `sending` | flag the lead only (D5) |
| 1b | replies in `queued` older than `NO_REPLY_SWEEP_MINUTES` (no kind filter: only system replies are ever `queued`), lead not anonymized | `send()` (claims) |
| 2 | leads with no first reply after `NO_REPLY_SWEEP_MINUTES` | fallback acknowledgment |

Gone: the "failed by the sweep" list, `STUCK_ERROR`, `failed_stuck_replies`, `_retry_stuck`'s second step. `_guarded` stays.

### 5.3 Atomic writes, Way B (Part 3, decision D2)
**`Repository.store_pipeline_result(lead_id, *, classification, decision, replies, now) -> StoredResult`** replaces the five commits of `IntakeService._write_result` (`set_classification`, `set_lead_status(CLASSIFIED)`, `acknowledge_lead`, `add_reply` for the draft, `set_lead_status(AWAITING_APPROVAL)`). One transaction, in this order, all under one row lock on the lead:
1. Lock the lead `FOR NO KEY UPDATE`; `AnonymizedLead` (new `ValueError` subclass) if anonymized.
2. Read status and `send_suppressed`; the lead must be `new` and not suppressed and have no first reply, else `InvalidTransition` (a late pipeline result after the deadline fallback is discarded, as today).
3. Store the classification fields and the router's flags.
4. Move the lead status by chaining `_move_status` calls through `LEAD_TRANSITIONS` (no commit between): `new -> classified` when the AI classified in a supported language; `-> acknowledged` when the first reply is an acknowledgment; `-> awaiting_approval` when a staff draft is stored. The transition table stays the single source of truth.
5. Insert the first reply as `queued` and the staff draft as `drafted`.
6. Commit once. Returns the ids: `StoredResult(first_reply_id: int | None, draft_id: int | None)`.
Any exception rolls everything back: either the whole result is stored or nothing is.

**`Repository.receive_lead(*, name, email, phone, message, consent_at, now, mailbox_key, max_per_hour, max_per_day) -> ReceivedLead(lead, created, suppressed)`** replaces `create_lead_if_new` + two counts + `suppress_lead` (three commits). One transaction:
1. `pg_advisory_xact_lock` on the **mailbox key** (not the dedup key). Two posts with the same email and message always have the same mailbox key, so this one lock covers both duplicate detection and the limit count, and posts of one mailbox queue up. The lock order is fixed (only this one lock is taken).
2. Duplicate? (same dedup key within 2 minutes) -> return `created=False`.
3. Insert the lead, count the mailbox's leads in the last hour and day (committed rows plus the new one: exact, because concurrent posts of that mailbox are serialized by the lock).
4. Over a limit: move `new -> acknowledged`, set `send_suppressed` and `needs_manual_review`, in the same transaction.
5. Commit once.
`create_lead_if_new` stays as the primitive for tests that only need a lead (it keeps its dedup-key lock behaviour through a shared private helper); `suppress_lead` and `SUPPRESS_TRANSITIONS` are deleted (their tests move to `receive_lead`).

**What `IntakeService` becomes.**
- `receive`: one call to `receive_lead`, then `Received(lead_id, run_pipeline = not suppressed)`.
- `process`: `try: stored = repo.store_pipeline_result(...)`; `except InvalidTransition`/`AnonymizedLead`: discard (log); `except Exception`: `_fallback`. Then send `stored.first_reply_id` if there is one. The `finally`, `_known_anonymized`, `_anonymized` and the "first_replies list" go.

**Acknowledged limits of atomicity (they stay, by design):** (a) lead committed, process dies before the pipeline ends -> sweep part 2; (b) first reply committed (`queued`), process dies before the claim -> sweep 1b; (c) claim, SMTP, record is inherently three steps (an email cannot be taken back).

### 5.4 Smaller removals
Deleted: `SEND_FROM`, `_REFUSE_TO_FAILED`, ruling 7's order-of-checks comment, `failed_stuck_replies`, `fail_stuck_reply`, `STUCK_ERROR`, `_known_anonymized`, `_anonymized`, `suppress_lead`, and the enum `isinstance` guards (D6). Kept: `unflagged_leads_with_stuck_replies` (D5) and `AWAITING_FIRST_REPLY` (used by `leads_without_first_reply`). Estimated net: about -150 lines of code and 10-15 tests deleted, 6-8 rewritten, 8-10 added (the race tests below).

## 6. Data and flows
- Migration `<rev>_queued_status`: (upgrade) drop and recreate `ck_replies_status` with `queued`; then `UPDATE replies SET status='queued' WHERE kind <> 'draft' AND status = 'drafted'` (the only illegal pair the old code could produce; the migration test also feeds it the other pairs to prove none exist); then create `ck_replies_kind_status`. (downgrade) drop `ck_replies_kind_status`, `queued -> drafted`, restore the old status CHECK. Retention is unaffected (it keys on `created_at` and `anonymized_at` and blanks text only).
- No new columns. No new personal data. Logs stay "ids and event names only".
- Docs that change with the code: A1 spec section 5 (reply status flow) and section 7; the 4b spec; `review/status-flows.md`; `review/4b-intake-sender.md` (overview table of methods, rulings 1, 7 and 9, parked items that disappear); README; PROJECT.md Decisions log (D1-D4 and the reasons); `devsetup/CATALOG.md` if anything is added.

## 7. Error handling
Unchanged in effect: an error may only lead to a flagged lead, a fixed acknowledgment, or staff review, never to a wrong automatic action. New in detail: a failed `store_pipeline_result` leaves nothing behind, so the fallback acknowledgment starts from a clean lead; a failed `receive_lead` is a 503 with nothing saved.

## 8. Failure modes (pre-mortem, plan mode of `/edge-case-probe`, 2026-10-25)
Ranked by impact x likelihood. "Test" = goes into the plan as a test written first; every concurrency row needs the two-session race, shown failing when the fix is removed (CLAUDE.md rule).

| # | Risk | What happens | Stopped by | Status |
|---|---|---|---|---|
| F1 | **Migration on existing rows:** a system reply in `drafted`, or any illegal kind/status pair, already exists when `ck_replies_kind_status` is created | Migration aborts half-way or the CHECK refuses to be created | Data fix first, CHECK second, in one migration; test: build a database at the old head with fake rows of every kind and status, upgrade, assert statuses; downgrade and re-upgrade | needs a test |
| F2 | **Staff draft sendable without approval** (the old ruling-7 hazard) | Unapproved medical text emailed | Table (enforced by `set_reply_status` -> `_move_status`, which is Python, not the database): `drafted` has no move to `sending`/`failed`; the DB CHECK `ck_replies_kind_status`: a draft is never `queued`, a system reply is never `drafted`/`approved`/`rejected`; `send()` on `drafted` returns `REFUSED` | needs a test (every status x kind pair against `send`; `set_reply_status(drafted draft, SENDING)` raises `InvalidTransition`; raw INSERT of an illegal pair raises `IntegrityError`) |
| F3 | **Re-claim race:** two sweeps read the same stuck reply | Two emails | `reclaim_stuck_reply` WHERE re-checks status, age and cap in one UPDATE; the loser gets 0 rows | needs a test (two threads, fails with the age check removed) |
| F4 | **Original sender wakes after a re-claim** (hung process, stuck limit 10 min vs SMTP timeout 10 s) | Possible second copy; the original's `_record` may also overwrite the new claim's result | Inherent (an email cannot be recalled); the stuck limit is 60x the timeout; `_record` stays conditional on `status = sending` | parked (documented, not fixable without a claim token; note for 4d) |
| F5 | **Mailbox lock replaces the dedup-key lock:** identical posts must still produce one lead; different messages from one mailbox must count exactly | Duplicate leads, or limit overshoot/undershoot | One advisory lock on `mailbox_key`; tests: N simultaneous identical posts -> 1 lead; N simultaneous different posts of one mailbox with limit L -> exactly L not suppressed, rest suppressed | needs a test (2 and 10 threads; fails with the lock removed) |
| F6 | **Lock ordering / deadlock:** `receive_lead` holds the mailbox lock, `store_pipeline_result` holds the lead row lock, retention holds `FOR UPDATE` on leads, then replies | Deadlock | `receive_lead` takes only the advisory lock then inserts a new row; the other two keep the existing order (lead, then replies); a fresh-agent attack pass with concurrency named, as CLAUDE.md requires after any locking change | needs a test + attack pass |
| F7 | **`store_pipeline_result` vs fallback race:** the 45 s deadline expires, the fallback acknowledgment and the late pipeline result both try to be the first reply | Two first replies (two emails) | The lead row lock plus the "no first reply yet" check in step 2; the loser gets `InvalidTransition` and writes nothing, now including no classification and no draft | needs a test (two threads; fails with the check removed; also assert nothing of the loser's partial data remains) |
| F8 | **Retention during `store_pipeline_result`** | Patient text stored on an anonymized lead | Step 1's `FOR NO KEY UPDATE` waits for the batch, then raises `AnonymizedLead`; everything rolled back | needs a test (the existing retention-race tests re-aimed at the new method) |
| F9 | **Commit succeeds but the answer is lost** (connection drops after COMMIT) | Process thinks the write failed, runs the fallback | The fallback's `acknowledge_lead` sees the first reply and returns False (flags the lead); the committed `queued` reply is sent by sweep 1b | needs a test (simulate: store, then call fallback) |
| F10 | **`receive_lead` crash after COMMIT, before the 202** | Lead saved, form got a 5xx, visitor resubmits | Duplicate window (2 min) answers 202 and does nothing; if later, a second lead is allowed (existing behaviour) | handled by design |
| F11 | **Hostile input:** huge or odd fields | Unchanged: `LeadIn` and the body limit stop it before the repository | handled by design (existing tests) |
| F12 | **Anonymized lead's `queued` reply** (retention blanks it, nobody closes it) | A `queued` row with `[anonymized]` text is never sent | Sweep 1b and `send()` skip anonymized leads (existing); the row stays | parked (was parked as "drafted forever") |
| F13 | **A `queued` reply on a suppressed lead** | Email the limit meant to stop | `acknowledge_lead` and `store_pipeline_result` refuse suppressed leads under the lock; suppression and the first reply cannot both exist | needs a test |
| F14 | **Docs vs code:** README, A1 spec section 5/7, 4b spec, `status-flows.md`, REVIEW rulings still say `drafted` for acknowledgments, "give up the claim", `STUCK_ERROR` | Reader trusts false text | Grep list in the last task: `drafted`, `STUCK_ERROR`, `fail_stuck_reply`, `failed_stuck`, `SEND_FROM`, `suppress_lead`, `_write_result` | handled by the plan's last task |
| F15 | **Pre-commit gate time** | Over 90 s blocks commits | The refactor deletes more tests than it adds; measure at the end; never `--no-verify` | needs measuring |
| F16 | **Cost abuse / money** | Unchanged: per-mailbox limit (now exact), global AI cap, slot cap | handled by design |
| F17 | **Reply with no address after the claim** | Claim spent, reply `failed`, no error text | Accepted (D4); test that it is `failed` with `error None`, not `sending` | needs a test |

Ranked by priority: F2, F3/F7 (double email), F5/F6 (locking), F1 (data), then the rest.

## 9. Safety type
Type A (customer-facing automation), patterns unchanged. The pattern that matters here is "an error may only lead to human review, never to a wrong automatic action". Not applicable: prompt-injection and redaction patterns (no AI input changes).

## 10. Models and context
None. No AI call changes.

## 11. Testing and evals
- Order: tests first for each part (red, then green). Race tests for F3, F5, F6, F7, F8 shown failing with the fix removed.
- Tests are moved, not just deleted: for each deleted test, say which new test or table row now covers its behaviour (in the plan).
- Whole suite stays green after every task; the full run time is recorded at the start and the end.
- After the build: `/edge-case-probe` in ATTACK mode (fresh agent, concurrency named), because locking and transaction boundaries change. No `/run-evals`.
- Tariq's review: a new file `review/refactor-simplification.md`, overview and method table first.

## 12. Build layers
| Part | What | Concepts | Done when |
|---|---|---|---|
| R1 | `queued` status, new table, CHECK, migration, `acknowledge_lead` inserts `queued`, `send()` and sweep 1b simplified, D4 | state machines enforce rules; migrating data with a CHECK | `SEND_FROM`, ruling 7, `_REFUSE_TO_FAILED` deleted; F1, F2, F13, F17 tests green |
| R2 | `reclaim_stuck_reply`, `_deliver`, delete the leftovers path | one statement instead of two transactions | F3 test green; `STUCK_ERROR` and `failed_stuck_replies` gone |
| R3 | `store_pipeline_result`, `AnonymizedLead`, simpler `process` | atomic writes with the commit kept in the repository | F7, F8, F9 tests green; `finally` and `_known_anonymized` gone |
| R4 | `receive_lead` with the mailbox lock, simpler `receive`, delete `suppress_lead` | one lock covering two rules | F5, F6 tests green |
| R5 | D6 guard removal, `SweepResult` keywords, grep for dead code | deleting code safely | no unused public method except `create_lead_if_new` (tests) |
| R6 | Docs (F14), CATALOG, PROJECT.md decisions, `review/refactor-simplification.md`, measure the gate time (F15), attack pass | | section 13 |

Each part ends with a green full suite and its own commit; R1 is first because its migration is the only schema change.

## 13. Definition of done
All tests pass and the gate stays under 90 s; the grep list of F14 is empty; the attack pass is triaged; README, A1 spec, 4b spec, `status-flows.md` and `review/4b-intake-sender.md` match the code; the PROJECT.md Decisions log has D1-D4 with reasons; `review/refactor-simplification.md` exists; Tariq has reviewed it.

## 14. Out of scope
Lead status redesign and `review_reason` (D7, decide in 4d); retrying `failed` acknowledgments in the sweep (D8); a claim token against a late original sender (F4); a separate outbox table (`replies` already is the outbox); the global emails-per-hour cap, per-IP limits and captcha (parked in 4b); cancellable LLM calls; the scheduler and deployment.
