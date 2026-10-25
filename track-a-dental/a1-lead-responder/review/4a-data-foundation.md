# Layer 4a: data foundation (Postgres, models, Alembic, repository, retention)

> Part of the A1 review set; index in [../REVIEW.md](../REVIEW.md). Built 2026-10-23, merged into `main` 2026-10-24. The section "Carry into the 4b/4c/4d plans" is still read before writing the 4c and 4d specs.

## What 4a is and how its pieces work together
**The problem it solves.** Layers 1-3 can take one message and produce a classification and a draft, but they forget everything when the program ends. A real clinic needs memory: who wrote, what the AI decided, what was sent, and what must be deleted later. 4a is that memory, plus the rules that keep it safe.

```
 later (4b+)                         4a (built)                        Docker
 -----------                         ----------                        ------
 API / dashboard / sender  --->  Repository  --->  SQLAlchemy models  --->  Postgres (data lives here)
                                 (the only door)   (tables as Python)       port 5433
                                      ^                   ^
 run_retention script ----------------+                   |
                                                  Alembic migration builds the tables
 sender (4b) ------------------------------------------------------------>  Mailpit (fake inbox, port 8025)
```

**The pieces, one line each:**
- **Docker** runs the two services we do not install by hand: Postgres (the database) and Mailpit (fake email inbox). One `docker compose up` and the setup is the same on any machine.
- **Models** (`db/models.py`) describe the tables as Python classes: leads, replies, staff users, audit log. Length limits and allowed status values are enforced by the database itself (CHECK constraints), so bad data cannot get in even if the code has a bug.
- **Alembic** is version control for the database structure. The migration creates the tables; a test checks that migration and models never drift apart.
- **Repository** (`repository.py`) is the only code that reads or writes those tables. Everything else asks it: "create this lead if it is new", "store the classification", "add a reply", "mark it approved". It holds the business rules in one place: duplicate detection (same email and message within 2 minutes is one lead, guarded by a database lock so two simultaneous submissions cannot both win), no changes to anonymised leads, cleaning of odd input.
- **Retention** (`retention.py` and the `run_retention` script) is the DSGVO promise: after 90 days it blanks the personal fields of old leads (name, email, phone, message, free-text reasons) and keeps only statistics (category, risk, status, dates), in batches so it scales.
- **Mailpit** is where 4b's sender will deliver replies during development, so nothing reaches a real person.

**What happens to one inquiry once 4b is on top (the flow):**
1. The website form posts to the API (4b). The API asks the Repository: new lead or duplicate? A duplicate is ignored; a new lead is stored.
2. The layer 1-3 pipeline classifies and drafts. The result goes back through the Repository (classification on the lead, draft as a reply with status `drafted`).
3. Staff approve in the dashboard (4d); the Repository changes the reply status; the sender (4b) delivers it, to Mailpit now and to a real mail server in production; the status becomes sent.
4. 90 days later the retention job anonymises the lead. Nothing in between could have kept personal data past that point, because the Repository refuses to attach new personal data to an anonymised lead.

**Why it is built this way (the concepts):** one door to the data (Repository) so rules cannot be bypassed; the database as the last line of defence (constraints, lock); schema changes as versioned code (Alembic); and privacy as a feature of the design, not a cleanup task. These are what you would explain to a client.

## How 4a was built (2026-10-23): the workflow in short
**What we built:** the data foundation under the AI core. Postgres and Mailpit in Docker, SQLAlchemy models, an Alembic migration, a `Repository` (leads, replies, duplicate detection, status changes) and the DSGVO retention job (anonymise leads after 90 days). No API, sender or dashboard yet; that is 4b onward.

**The steps, in order:**
1. **Plan first.** The 4a plan (`docs/superpowers/plans/2026-10-23-a1-layer-4a-data-foundation.md`) came out of the spec's failure modes (spec section 14), so the risky cases were test cases before any code existed.
2. **Isolated branch.** Work ran on `feat/a1-layer-4a` so `main` stayed clean.
3. **One fresh agent per task** (5 tasks: infra and models, leads repo, replies repo, retention, docs). Each implementer wrote tests first, then code, then committed.
4. **A reviewer after every task** checked it against the plan and for code quality. Findings went back to the implementer for a fix round (example: a race test that proved nothing was replaced by a real advisory-lock test).
5. **A final whole-branch review** with the strongest model. It found real bugs (a crash on a NUL byte, retention keeping free text about patients). One fix wave.
6. **Attack probe** (`/edge-case-probe`): a fresh agent that had never seen our reasoning tried to break the code and found ten issues. We fixed four (dedup-key collision, retention bind limit, personal data re-entering anonymised leads, NUL in `language`), each with a regression test, and parked the other six in the lists below.
7. **Gate on every commit:** `.githooks/pre-commit` runs the test suite (docs-only commits skip it). Nobody bypasses it.
8. **Docs with the code:** README, CATALOG and this file were updated in the same pass. Result: 347 tests pass.

**How the parts fit together:**
- The **spec** says what must hold; the **plan** turns it into tasks; **agents** build; **reviewers** and the **attacker** check from outside; the **hook** keeps the suite green; **REVIEW.md** is where their open questions come to you.
- Why this many checks: agents share blind spots with whoever briefed them. The reviewer sees the diff, the attacker sees only the interface, and you are the last independent pair of eyes.
- Your review is not redoing the tests. It is judging the decisions: is the schema right, is the dedup rule right, is the DSGVO promise honest, and which parked items matter before 4b.

## 4a: your review, in this order (about 1.5 to 2 hours; all paths under `track-a-dental/a1-lead-responder/`)
**Merged into `main` on 2026-10-24 (Tariq's decision). Steps 3, 6 and 7 below were not walked through before the merge; the code is on `main` and they can still be done, for example together with the 4b plan.** Branch `feat/a1-layer-4a`, the commits on top of `df0c49e` (checked out in the main folder; compare with `git diff main..feat/a1-layer-4a`). Tick each box when you have read it and are happy, or write a note.
- [ ] **1. Run it (10 min).** Follow the README Run table: `docker compose up -d postgres mailpit`, `alembic -c track-a-dental/a1-lead-responder/alembic.ini upgrade head`, `uv run pytest tests/db`, then `uv run python -m lead_responder.scripts.run_retention` against the dev database (an implementer already ran it once on the empty dev DB, see the README "Checked on" line; run it yourself on a few fake old leads to see it work). Open Mailpit at http://localhost:8025 (empty was expected in 4a; since 4b the end-to-end tests and the API send mails there).
- [ ] **2. Schema (20 min).** `lead_responder/db/models.py` (182 lines), then `alembic/versions/a30306790587_init.py`. Check against spec section 5: columns, the two additions (`replies.edited`, `leads.dedup_key`), the CHECK constraints, the indexes, and the two transition tables (`LEAD_TRANSITIONS`, `REPLY_TRANSITIONS`: do they match the spec's status flow?). Note the length limits are CHECKs, not VARCHAR (see notes below).
- [ ] **3. Leads repository (30 min).** `lead_responder/repository.py` (355 lines): `create_lead_if_new` first (advisory lock, then lookup, then insert, one transaction), then `set_classification`, `set_lead_status` (and the shared `_move_status` it uses). Ask yourself: what happens if two submissions arrive at once, and what does the dedup key contain?
- [ ] **4. Replies repository (10 min).** Same file: `add_reply`, `set_reply_status`, `replies_for`. `set_reply_status` is where the "error cleared on every change" decision below comes from, and where "sent is final" is enforced (conditional UPDATE, see `_move_status`; tests: `test_sent_is_final`, `test_stale_session_cannot_send_twice`, `test_two_threads_racing_to_send_exactly_one_wins`).
- [ ] **5. Retention (20 min).** `retention.py` (15 lines) and `anonymize_older_than` in `repository.py`: batches, the 90-day boundary, what is blanked and what is kept as statistics, and why no clean-up pass is needed (the write paths check the lead under a `FOR SHARE` lock: `_anonymized_under_lock`). This is the DSGVO promise you would make to a client, so read it slowly.
- [ ] **6. The tests that matter (20 min).** `tests/db/test_repository_leads.py` (the advisory-lock test and the dedup-collision test), `tests/db/test_retention.py` (boundary, batches, the two-runs-at-once test, the "write during a retention batch" tests), `tests/db/test_schema.py` (the drift guards). Pick one behaviour and ask "would this test fail if I deleted that line?" (the reviews mutation-checked the important ones).
- [ ] **7. The commit gate (10 min).** `.claude/hooks/precommit.py` (`summarize`, `report`) and the `GIT_*` env scrub in `.claude/hooks/test_hooks.py` (an out-of-plan fix, see `devsetup/CATALOG.md`).
- [ ] **8. Decide the open items in "4a notes" below** (audit row in the same transaction; error clearing and "sent is final" are settled, check the two transition tables instead) or leave them for the 4b/4c plans; then skim the Parked lists and the attack-probe section at the bottom.
- [ ] **9. Then choose:** merge `feat/a1-layer-4a` into `main` (or open a PR), and start the 4b plan.

## 4a notes: to decide or know (2026-10-23)
- [x] **Error is cleared on every status change: no longer a problem (2026-10-23).** `set_reply_status` still sets `error` to the given value or None each time, but with the transition tables the risky move (`failed` -> `approved`, which dropped the "may have been sent" warning) no longer exists: from `failed` a reply can only be claimed again (`failed -> sending` since 4b; the claim, then `sent` clears the error, and the audit log in 4c records the resend). Nothing to decide; keep as is.
- [x] **Retention blanks `leads.ai_reason` and `replies.error`** (done in the final-review fixes; `error` may hold a recipient address, `ai_reason` is free text about the patient).
- [ ] **Length limits are CHECK constraints** on text columns (`char_length`), not `VARCHAR(n)`. Overflow raises `IntegrityError`; since 4b the API model `LeadIn` checks first (clear 422), using the same numbers from `limits.py`.
- [ ] **Sessions:** `make_session_factory` uses `expire_on_commit=False`. A caller whose Session keeps the default (`True`) gets expired objects back from the repository (attributes reload on access).
- [x] **Staff login: own table for now (decided 2026-10-23).** `staff_users` with argon2, lockout, CLI-managed accounts; not an external auth service (no extra vendor under DSGVO, nothing more to run). 4c keeps login in one module so OIDC (Microsoft/Google) can replace it later as a paid add-on. Recorded in the PROJECT.md Decisions log; revisit at the start of 4c.
- [ ] **Duplicates are only suppressed, not blocked:** the advisory lock plus the 2-minute window stops identical simultaneous or quick repeat submissions; it is not rate limiting.

## Carry into the 4b/4c/4d plans (decided or found during the 4a review, 2026-10-23)
Read this before writing the next spec or plan. The rule behind most items: **each rule lives in one layer** (input rules in the API model, business rules in a service, database access and its own commit in the repository, hard limits in the database; Tariq's direction 2026-10-24; see "Service layer in 4b" below).
- **Move input validation out of the repository in 4b (Tariq's call).** Where each rule belongs:

  | Rule now in `repository.py` | Home after 4b | Note |
  |---|---|---|
  | Length limits (`_check_len`, `MAX_*`) | API request model; DB CHECKs stay as the last line | Define each limit once in a shared constants module used by the model and `db/models.py`; delete the `MAX_*` copies; move the "too long" tests to the API layer |
  | Strip NUL (`_clean`) | API request model (text cleaner) | Anything bypassing the API then gets a DB error, not silent cleanup. `dedup_key` must still be computed on cleaned text |
  | Enum `isinstance` checks | Type hints plus the DB status CHECKs | Only the nicer error message is lost |
  | `_require_aware` | Keep | A naive datetime would silently mean server local time |
  | Duplicate detection (advisory lock plus 2-minute lookup) | The decision ("is it a duplicate?") moves to the service if 4b adds one; the lock and the lookup stay repository queries | A unique constraint cannot express a time window |
  | Refusing personal data on an anonymized lead | Option: a CHECK on `leads` (if `anonymized_at` is set, email, message, phone, name, `dedup_key`, `ai_reason` are blank), via a new Alembic migration | Covers `leads` only. `replies` would need a trigger (cross-table), which is more complex than the Python guard, so keep `_refuse_if_anonymized` for replies |

  Keep `_clean` and the other storage rules working until the API model exists: today the repository is the only thing in front of the database, and the attack-probe regression tests (`tests/db/`) are the check that nothing was lost in the move.
- **Service layer in 4b (Tariq's direction, 2026-10-24; settle the details in the 4b spec).** Layering: the API validates input; a **service** holds the business rules (the duplicate decision, which status moves are allowed, refusing anonymized leads, claim before send); the **repository** is database access (queries, locks, and its own commit: one method = one transaction, as in 4a; decided by Tariq); the database holds hard limits. Today `Repository` also holds the business rules, so moving them is the work. The 4c audit row then goes inside the repository methods (see Parked). Cost: one more layer and moving tests; do it when 4b adds its first caller.
- **Concurrency: conditional (compare-and-set) status changes: DONE in 4a (decided 2026-10-23, Tariq).** `set_reply_status` and `set_lead_status` used to read, then write, so the last writer won: two sessions could both mark a reply `SENT` (two emails to one patient), and `sent` could fall back to `failed` (was parked F5, "sent is final"). Now both go through `_move_status`: one statement, `UPDATE ... SET status=:new WHERE id=:id AND status IN (the statuses that may move to :new)`, and zero rows raises `InvalidTransition` (a `ValueError`) or `LookupError` for a missing row. The allowed moves are the two tables `LEAD_TRANSITIONS` / `REPLY_TRANSITIONS` in `db/models.py`; `sent` and `rejected` replies and `done` leads are final. **For 4b/4d:** the sender and the approve action must call `set_reply_status` and handle `InvalidTransition` (4d: show "already handled", do not send). **Provisional table entries to confirm in 4b:** `new -> acknowledged` (classifier failure) and `acknowledged -> done` (routes 1 and 2, handled by phone) are my reading of the spec; if the pipeline needs another move, change the table (one place) and its test. The error-clearing question is settled by the same tables (see 4a notes).

  | Method | Concurrency today | Plan |
  |---|---|---|
  | `create_lead_if_new` | Safe: advisory lock makes "look, then insert" atomic | Keep. Optional database-native alternative, untested: an `EXCLUDE USING gist` constraint on (`dedup_key`, 2-minute range) via `btree_gist`; it would remove the lock and lookup from Python for a migration and an extension |
  | `set_reply_status`, `set_lead_status` | Safe (since 2026-10-23): transition tables plus one conditional UPDATE; tests include a two-thread race and a stale-session case | Keep; 4b/4d call it and handle `InvalidTransition` |
  | `set_classification` | Since 4b: only while the lead is `new` (`InvalidTransition` otherwise), status read after the `FOR NO KEY UPDATE` lock, so a late pipeline result after the deadline fallback writes nothing (test `test_set_classification_waits_for_an_acknowledgment_then_refuses`, fails with the check removed or moved before the lock). Two classifications of a `new` lead: last write wins | Keep |
  | `acknowledge_lead` (4b) | The lead-status move and the first reply's insert in one transaction under `FOR NO KEY UPDATE` on the lead; a second writer sees the first reply and gets `InvalidTransition` (race test fails with the check removed; with `FOR SHARE` instead it deadlocks) | Keep; every writer of a first acknowledgment goes through it |
  | `suppress_lead`, `flag_for_review` (4b) | `suppress_lead`: one conditional UPDATE, only from `new`. `flag_for_review`: one UPDATE, no read first | Keep. `acknowledge_lead` also refuses a `send_suppressed` lead under its lock (build ruling 4), and the sweep query skips suppressed leads |
  | `add_reply` | Safe (since 2026-10-23): the check takes `FOR SHARE` on the lead, so it waits for a running retention batch and then refuses | Keep. The earlier retention sweep was removed. `set_classification` uses the same check with `FOR NO KEY UPDATE` (it updates the lead row; `FOR SHARE` there deadlocked, found by attack probe 2) |
  | `anonymize_older_than` | Safe: `FOR UPDATE` batches, a second run waits and finds nothing | Keep |
- **4b design gap: claim the reply BEFORE sending (found 2026-10-23; fixed in 4b task 2 with option (a): status `sending`, `drafted|approved|failed -> sending` is the claim).** The conditional UPDATE protects the *recording* of a status, not the send itself. The sender's order is: read the reply, send the email, then `set_reply_status(SENT)`. Two workers (or two Resend clicks) that both read `approved`/`failed` both send; only the second *recording* is refused, after the patient already got two emails. The fix is a claim that one session wins before the send. Options for 4b: (a) a new status `sending` (migration, CHECK and tables change): `approved -> sending` is the claim, then `sent` or `failed`; (b) allow `failed -> approved` so a resend is a claim too (then the "may have been sent" error is cleared on resend, which the audit log records; this reverses the "error clearing is settled" note above for that one move). No `expected=` argument is needed: the `status IN (sources)` check already compares against the status at update time.
- **Retention concurrency (checked 2026-10-23):** two retention runs at once handle each lead exactly once (`FOR UPDATE` on each batch); test `test_two_retention_runs_at_once_handle_each_lead_exactly_once` fails 5 of 5 times when the lock is removed. A write during a run is covered by the lock: a plain "is it anonymized?" read did NOT wait for the batch (probe: a reply with patient text was stored on a lead retention had just blanked), so the check now takes `FOR SHARE` and waits (tests `test_add_reply_during_a_retention_batch_waits_then_refuses` and two more; they fail without the lock). The retention sweep was removed because of this. Residual: someone writing personal text with raw SQL, bypassing the repository, is no longer cleaned up automatically. Still open: no `lock_timeout` (parked F7).
- **Scheduling retention is not built yet.** `scripts/run_retention.py` is the unit a scheduler runs; nothing schedules it. Recommended: daily (the job is idempotent and cheap), because the promise to a client is "personal data is gone after 90 days"; every 5 days would keep data up to 95. Pick the scheduler with the deployment layer (cron or a Docker job on the VPS, Windows Task Scheduler for the local demo, or an n8n schedule trigger in layers 5-6) and have it alert when the job fails or the count looks wrong.
- **Staff login stays our own table for now (decided).** 4c keeps login in one module so OIDC can replace it later (PROJECT.md Decisions log). `staff_users` stays even with external auth: `approved_by` and the audit log point at it.
- **Lead status after routes 1 and 2 was unspecified; provisional answer now in the code.** Routes 1 (classifier failed) and 2 (language not DE/EN) send only the acknowledgment and create no draft, so the spec's flow `acknowledged -> awaiting_approval` did not cover them. The transition table now lets them stay `acknowledged` (flagged `needs_manual_review`) and later go `acknowledged -> done`; 4b confirms or changes it. 4d's dashboard must list these leads (flagged) either way. Route 4 (routine during opening hours) and a downgraded auto-reply behave like route 3 (acknowledgment plus draft).
- **Lead status vs reply kind:** `leads.status` is one value for the lead; `replies.kind` (`auto_reply`, `acknowledgment`, `draft`) is what was sent or drafted. A sensitive lead has two replies (the acknowledgment sent at once, the draft waiting).
- **Indexes with no query yet:** `ix_leads_status` and the status half of `ix_replies_lead_id_status` are for the 4d dashboard queues. Keep them if 4d uses them, otherwise cut them.
- **4b flow to build against** (from the spec, not yet run): form -> API validates -> `create_lead_if_new` (duplicate: stop) -> pipeline -> `set_classification` and `add_reply` -> acknowledgment sent at once -> staff approve in 4d -> sender delivers (Mailpit in development) -> retention after 90 days. `add_reply` and `set_classification` raise `ValueError` for an anonymized lead; 4b must handle it.
- **Mailpit** is the fake inbox for development (SMTP 1025, web 8025). Production changes only the SMTP settings in `.env`.

## Parked: 4a small items
- ~~"sent is final" is not enforced~~ fixed 2026-10-23: transition tables plus a conditional UPDATE in `set_reply_status` / `set_lead_status` (see "Carry into the 4b/4c/4d plans"). The error-clearing question is settled too (see 4a notes).
- **One commit per repository method** vs spec section 14 row 13 (audit row in the same transaction): decided 2026-10-24 (Tariq): the commit stays in the repository, so 4c writes the audit row inside the repository methods (approve, edit, reject, resend), in the same transaction as the change. A unit of work (the service commits once) was the alternative.
- **Retention can blank a pending draft.** (Since the attack fix wave `add_reply` and `set_classification` also raise `ValueError` for an anonymized lead; `set_reply_status` stays allowed for status moves, but its `error` text is dropped for an anonymized lead.) The 4b sender and the 4d approve action must refuse leads with `anonymized_at` set (or an empty email).
- **`run_retention.py` uses `Session(engine)`, not `make_session_factory`.** 4b must use `make_session_factory`, one session per request.
- Dedup lower-cases the email but does not strip it (leave to 4b form validation). `set_classification` stores the model's raw language (cut to the column width), not the normalised `decision.language`.
- `DATABASE_URL_TEST` could point at the dev database and the tests run `drop_all`: consider a guard that the database name ends in `_test`. Two checkouts running the suite at once share `leads_test`. Length-CHECK drift is not caught by `compare_metadata`.
- ~~Retention updates with `IN (ids)`~~ fixed in the attack fix wave: batches of `RETENTION_BATCH_SIZE` (5000), one transaction each.
- The 120 s duplicate window has no upper bound on lead timestamps by design (a lead stamped later than `now` still counts); revisit if clocks can drift.
- `run_retention` does not call `engine.dispose()`; harmless for a one-shot script, add it if the script grows.

## Attack probe of layer 4a, 2026-10-23 (fresh agent): fixed and parked
Fixed with regression tests (`tests/db/`): F3 dedup key joined email and message with a newline, so two different patients could collide (separator is now NUL, computed after cleaning); F1 retention used one `IN (ids)` list (65535 bind-parameter limit), now batched; F2 personal data could re-enter an anonymized lead (guards in `add_reply` / `set_classification`; the retention sweep first added for this was later replaced by a `FOR SHARE` lock on the check and by dropping the error text in `set_reply_status`); F6a NUL in `language`.

Parked (all from the 2026-10-23 attack probe):
- **F4 stale identity map:** fixed 2026-10-24 for `get_lead` and `replies_for` (`populate_existing`). Still true for objects a caller keeps and for `_require`; 4b/4d: one short session per request.
- ~~**F5 no reply state machine**~~ fixed 2026-10-23 together with "sent is final": `sent` -> `sent` is refused (`sent_at` no longer moves) and two sessions cannot both mark a reply `SENT`.
- **F6b no length cap** on `ai_reason`, reply `body`, `ai_body` and `error` (50 MB accepted); lone surrogates raise `UnicodeEncodeError`.
- **F7 no lock or statement timeout anywhere** (retention and writers wait as long as another transaction holds a row; `make_engine` sets none): add `lock_timeout` and `idle_in_transaction_session_timeout` in `make_engine` (for example `-c lock_timeout=5s -c idle_in_transaction_session_timeout=60s`) and consider `SKIP LOCKED` for retention. The part about `get_lead` leaving a transaction open is fixed (2026-10-24).
- **F8 read methods did not roll back:** fixed 2026-10-24 (commit/rollback like the writers). Still open: wrong-type ids raise `DataError` / `ProgrammingError`, not `ValueError`; belongs to 4b input validation.
- **F9 pre-commit gate:** `git mv code.py code.md` is treated as docs-only (add `--no-renames`); the `.env` block does not cover `.envrc`, `prod.env`, `.ENV`.
- **F10 misc:** channel not validated; empty name/email/message accepted; `anonymize_older_than` accepts a future cutoff (wipes everything); no per-lead erasure method (DSGVO Art. 17); `audit_log.details` is not blanked by retention; `alembic downgrade base` drops all data; docker-compose publishes 5433 on all interfaces with password `leads` (dev only, bind to 127.0.0.1 before any shared host); dedup is bypassable by spelling variants until 4b rate limits.

## Attack probe 2 of layer 4a: concurrency and edge cases, 2026-10-24 (fresh agent, `leads_probe` database)
Scope: every `Repository` method against each other, retention, sessions, inputs. All findings were reproduced by me before fixing.

Fixed with regression tests (`tests/db/`):
- **High: two `set_classification` calls on the same lead deadlocked** (52 of 60 rounds; my own `FOR SHARE` change from the same day: two share-lock holders each wait for the other before they can update the lead row). Now `set_classification` takes `FOR NO KEY UPDATE`; 60 of 60 rounds clean, all method pairs re-run without errors.
- **Reads left the transaction broken or open** (`get_lead`, `replies_for`): a failed read made the next write fail with `InFailedSqlTransaction`, and a successful one left the connection "idle in transaction" (blocks migrations). They now commit/roll back like the writers.
- **Stale cache after retention** (a long-lived session showed erased patient data): both reads reload with `populate_existing`.
- **Retention `batch_size` above the bind limit** crashed every run without anonymizing: now capped at 60000.
- Comments/docs that contradicted the behaviour of two concurrent retention runs (they share the batches; each lead once) were corrected.

Parked (new, from this probe; the 4b validation model is the home for most of them):
- No size caps on reply `body`/`ai_body`, `ai_reason`, `error` (5 MB accepted); see F6b.
- `add_reply` accepts any starting status (`approved` without an approver, `rejected`, `sent`); `set_reply_status(APPROVED)` accepts no `approved_by`. 4b/4c: restrict to `drafted` plus system-made `sent`/`failed`, require the approver.
- `set_reply_status(SENT, now=...)` accepts a `now` before the reply's `created_at` (negative latency in the statistics).
- Dedup misses a leading space in the email and a double inner space in the message (strip the email, collapse whitespace before hashing, in 4b validation).
- `channel` is not validated (`"fax"` stored; 31 characters gives `DataError`); empty name/email/message accepted.
- `anonymize_older_than` accepts a future cutoff and a `now` before the cutoff; only `retention.run` checks `days > 0`.
- Not tested: a full Postgres restart mid-batch; clock skew between several app instances (an instance whose clock runs more than 2 minutes fast lets a duplicate through); an interrupt during `rollback()` itself.

Held up: retention against every writer and itself (20 to 150 rounds per pair, no personal data on an anonymized lead, no missed replies), simultaneous identical submissions (exactly one lead), advisory-lock id collisions (only a wait), the status compare-and-set (one winner, `sent` stays final), every write method on every error path (rolled back, no leftover locks), NUL, emoji, RTL, zero-width text, lone surrogates and naive datetimes.
