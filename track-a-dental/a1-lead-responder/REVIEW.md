# Review & Next Steps (Tariq's checklist)

> A1 review file; each project keeps its own REVIEW.md next to its README. Central place for work that needs **Tariq's review or decision**. Tick items off, add notes. Update it whenever a step finishes or new review work appears.
> Last updated: 2026-10-23

## Where we are
- A1 layers 1–3 (core logic) built; the mocked unit tests pass; Python pinned to 3.12 (`.python-version`).
- First live runs on 2026-10-22 (Haiku 4.5 classifier, Sonnet 5.5 drafter, effort low):
  - Classifier: category 44/45, risk 45/45, language 45/45, sensitive→routine **0**, injection failures **0**, cost $0.057.
  - Drafter: 18 auto-replies, 0 guardrail rejections, 0 API errors, cost $0.445 (about $0.01 per inquiry).
- Layer 4a exists (database, repository, retention). No API server, sender, dashboard or n8n yet.

## Concepts to understand (ask me about anything unclear)
- **Pipeline:** redact (code) → classify (Haiku) → decide (code) → draft (Sonnet) → guardrails (code) → send or staff queue.
- **Workflow vs agent:** in a workflow, *our code* decides the steps; in an agent, *the LLM* decides the next step in a loop. A1 is a workflow on purpose: the safety decision must be deterministic code.
- **Context engineering:** better context (facts, style guides, examples) can replace a bigger model for writing tasks, but not for judgment on ambiguous input. We test this instead of assuming it.

## Steps

### 1. Read the code (about 1 hr) — Tariq
- [x] Read in this order: `redactor.py`, `classifier.py`, `router.py`, `drafter.py`, `guardrails.py`, `pipeline.py` (all in `track-a-dental/a1-lead-responder/lead_responder/`).
- [ ] Run `uv run python -m lead_responder.scripts.try_inquiry --name Anna --message "..."` with a routine, a pain, and a Turkish message; watch each branch.
- [x] Write down anything unclear and ask.

### 2. Review the eval results (about 1 hr) — Tariq
- [x] Open the newest `track-a-dental/a1-lead-responder/eval_runs/drafts-*.md` (latest: `drafts-20261022-2323.md`) and read 5–10 replies. Check facts against `faq.md`, tone, and the "no diagnosis / no prices" rules. Note findings below.
- [x] Rule on label `i05` (expected `medical_question`, Haiku said `pain_emergency`; both sensitive, so routing is the same).
- [x] Skim all labels in `data/eval_inquiries.jsonl`. The spec says every label is reviewed by you before the score counts.
- [x] Review `data/faq.md` and `data/practice.yaml` for correctness.

Notes:
-

### 3. Record the baseline — Claude
- [x] Fill the README "Eval results" section with the numbers above (date, models, cost). Done 2026-10-22, re-run after the router/guardrail/prompt changes.
- [ ] Fix `.env.example` (`ANTHROPIC_API_KEY=` with an empty value).
- [ ] Commit `PROJECT.md`, `.python-version`, README, `.env.example`, and this file.

### 4. Model experiment (first dev-setup template) — Claude runs, Tariq judges
- [x] Re-run the drafter eval with `DRAFTER_MODEL=claude-haiku-4-5` and compare with Sonnet side by side. Done 2026-10-22: Haiku passes the guardrails at 2.4x lower cost but breaks soft rules in pain drafts; Sonnet stays. Not yet tested per function (`safe_info` only).
- [ ] Try Sonnet with and without effort.
- [x] Write the result to `devsetup/model-choice.md`: task, model, effort, why, measured cost.

### 5. Context experiment — Claude builds, Tariq judges
Hypothesis: better context lets a cheaper model write as well as Sonnet.
- [ ] Add per-language style guides (`style_de.md`, `style_en.md`), loaded by code based on the detected language, so each call carries only what it needs.
- [ ] Add 3–5 few-shot example replies per category (the biggest likely quality lever).
- [ ] Re-run the drafter eval with Haiku and with Sonnet; compare to step 4.
- [ ] Record the outcome in `devsetup/model-choice.md` and decide the drafter model.
- Not planned: subagents (no isolation or parallelism need here) and runtime Agent SDK skills (code already knows the language). Revisit in Track C.

### 6. Small dev-setup item — Claude
- [ ] Hook that blocks committing `.env` or anything containing an `sk-ant-` key.
- [ ] Start `devsetup/CATALOG.md` (what, why, when it helped).

### 7. Layer 4 — after the drafter model is decided
- [x] 4a data foundation: Postgres + Mailpit in Docker, ORM models, Alembic, repository, retention (built 2026-10-23; your review is still open, see "4a notes" below).
- [ ] 4b intake API and sender, 4c staff login and audit log, 4d HTMX dashboard, 4e demo site and end-to-end. See spec section 9.
- [ ] Form/API validation (found by the edge-case probe): cap the message size at the API (the pipeline also rejects > 5000 chars for the AI), cap and sanitise the name (max length, no newlines; it is inserted into reply bodies), escape everything on display (Jinja autoescape stays ON).
- [ ] Dashboard shows two queues: sensitive drafts first, routine drafts below (decided 2026-10-22). Every lead has a draft to review, so staff never write from scratch.
- [ ] Then layers 5–6 (n8n, staff notifications), ROI math in the README, demo video.

### 8. Proposed feature: knowledge feedback loop (not decided, after layer 4)
Idea: the assistant gets smarter with use, with a human approving every change.
- Staff open a draft: approve as is (logged as good), edit then send (the edit is a quality signal), or click "Suggest for FAQ".
- A suggestion is staff-written wording, goes to a pending list, and an approver turns it into a versioned FAQ entry. Approved edited replies can also become few-shot examples.
- Gap detection: routine questions the FAQ cannot answer are listed as "frequently asked, not in the FAQ".
- Rules: never auto-add; store staff wording, never patient text (DSGVO Art. 9, injection risk); versioned with audit log and rollback; suggest vs approve may be different roles.
- Fits as layer 4b or layer 7 (needs dashboard, DB, audit log). The same approval flow later feeds a vector index in A2 (RAG).
- ROI angle: lowers upkeep effort, supports a monthly retainer.
- [ ] Decide: should the practice owner or any staff member approve entries? Add to the A1 spec once decided.

### What 4a is and how its pieces work together
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
2. The layer 1-3 pipeline classifies and drafts. The result goes back through the Repository (classification on the lead, draft as a reply with status pending).
3. Staff approve in the dashboard (4d); the Repository changes the reply status; the sender (4b) delivers it, to Mailpit now and to a real mail server in production; the status becomes sent.
4. 90 days later the retention job anonymises the lead. Nothing in between could have kept personal data past that point, because the Repository refuses to attach new personal data to an anonymised lead.

**Why it is built this way (the concepts):** one door to the data (Repository) so rules cannot be bypassed; the database as the last line of defence (constraints, lock); schema changes as versioned code (Alembic); and privacy as a feature of the design, not a cleanup task. These are what you would explain to a client.

### How 4a was built (2026-10-23): the workflow in short
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

### 4a: your review, in this order (about 1.5 to 2 hours; all paths under `track-a-dental/a1-lead-responder/`)
Branch `feat/a1-layer-4a`, the commits on top of `df0c49e` (checked out in the main folder; compare with `git diff main..feat/a1-layer-4a`). Tick each box when you have read it and are happy, or write a note.
- [ ] **1. Run it (10 min).** Follow the README Run table: `docker compose up -d postgres mailpit`, `alembic -c track-a-dental/a1-lead-responder/alembic.ini upgrade head`, `uv run pytest tests/db`, then `uv run python -m lead_responder.scripts.run_retention` against the dev database (an implementer already ran it once on the empty dev DB, see the README "Checked on" line; run it yourself on a few fake old leads to see it work). Open Mailpit at http://localhost:8025 (empty is fine; nothing sends yet).
- [ ] **2. Schema (20 min).** `lead_responder/db/models.py` (182 lines), then `alembic/versions/a30306790587_init.py`. Check against spec section 5: columns, the two additions (`replies.edited`, `leads.dedup_key`), the CHECK constraints, the indexes, and the two transition tables (`LEAD_TRANSITIONS`, `REPLY_TRANSITIONS`: do they match the spec's status flow?). Note the length limits are CHECKs, not VARCHAR (see notes below).
- [ ] **3. Leads repository (30 min).** `lead_responder/repository.py` (355 lines): `create_lead_if_new` first (advisory lock, then lookup, then insert, one transaction), then `set_classification`, `set_lead_status` (and the shared `_move_status` it uses). Ask yourself: what happens if two submissions arrive at once, and what does the dedup key contain?
- [ ] **4. Replies repository (10 min).** Same file: `add_reply`, `set_reply_status`, `replies_for`. `set_reply_status` is where the "error cleared on every change" decision below comes from, and where "sent is final" is enforced (conditional UPDATE, see `_move_status`; tests: `test_sent_is_final`, `test_stale_session_cannot_send_twice`, `test_two_threads_racing_to_send_exactly_one_wins`).
- [ ] **5. Retention (20 min).** `retention.py` (15 lines) and `anonymize_older_than` in `repository.py`: batches, the 90-day boundary, what is blanked and what is kept as statistics, and why no clean-up pass is needed (the write paths check the lead under a `FOR SHARE` lock: `_anonymized_under_lock`). This is the DSGVO promise you would make to a client, so read it slowly.
- [ ] **6. The tests that matter (20 min).** `tests/db/test_repository_leads.py` (the advisory-lock test and the dedup-collision test), `tests/db/test_retention.py` (boundary, batches, the two-runs-at-once test, the "write during a retention batch" tests), `tests/db/test_schema.py` (the drift guards). Pick one behaviour and ask "would this test fail if I deleted that line?" (the reviews mutation-checked the important ones).
- [ ] **7. The commit gate (10 min).** `.claude/hooks/precommit.py` (`summarize`, `report`) and the `GIT_*` env scrub in `.claude/hooks/test_hooks.py` (an out-of-plan fix, see `devsetup/CATALOG.md`).
- [ ] **8. Decide the open items in "4a notes" below** (audit row in the same transaction; error clearing and "sent is final" are settled, check the two transition tables instead) or leave them for the 4b/4c plans; then skim the Parked lists and the attack-probe section at the bottom.
- [ ] **9. Then choose:** merge `feat/a1-layer-4a` into `main` (or open a PR), and start the 4b plan.

### 4a notes: to decide or know (2026-10-23)
- [x] **Error is cleared on every status change: no longer a problem (2026-10-23).** `set_reply_status` still sets `error` to the given value or None each time, but with the transition tables the risky move (`failed` -> `approved`, which dropped the "may have been sent" warning) no longer exists: from `failed` a reply can only go to `sent` (the error is then correctly cleared; the audit log in 4c records the resend) or `failed` again (the new error replaces the old one). Nothing to decide; keep as is.
- [x] **Retention blanks `leads.ai_reason` and `replies.error`** (done in the final-review fixes; `error` may hold a recipient address, `ai_reason` is free text about the patient).
- [ ] **Length limits are CHECK constraints** on text columns (`char_length`), not `VARCHAR(n)`. Overflow raises `IntegrityError`; the repository checks first and raises a clear `ValueError`.
- [ ] **Sessions:** `make_session_factory` uses `expire_on_commit=False`. A caller whose Session keeps the default (`True`) gets expired objects back from the repository (attributes reload on access).
- [x] **Staff login: own table for now (decided 2026-10-23).** `staff_users` with argon2, lockout, CLI-managed accounts; not an external auth service (no extra vendor under DSGVO, nothing more to run). 4c keeps login in one module so OIDC (Microsoft/Google) can replace it later as a paid add-on. Recorded in the PROJECT.md Decisions log; revisit at the start of 4c.
- [ ] **Duplicates are only suppressed, not blocked:** the advisory lock plus the 2-minute window stops identical simultaneous or quick repeat submissions; it is not rate limiting.

### Carry into the 4b/4c/4d plans (decided or found during the 4a review, 2026-10-23)
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
  | `set_classification` | Last write wins; runs once per lead | Fine as is |
  | `add_reply` | Safe (since 2026-10-23): the check takes `FOR SHARE` on the lead, so it waits for a running retention batch and then refuses | Keep. The earlier retention sweep was removed. `set_classification` uses the same check with `FOR NO KEY UPDATE` (it updates the lead row; `FOR SHARE` there deadlocked, found by attack probe 2) |
  | `anonymize_older_than` | Safe: `FOR UPDATE` batches, a second run waits and finds nothing | Keep |
- **4b design gap: claim the reply BEFORE sending (found 2026-10-23).** The conditional UPDATE protects the *recording* of a status, not the send itself. The sender's order is: read the reply, send the email, then `set_reply_status(SENT)`. Two workers (or two Resend clicks) that both read `approved`/`failed` both send; only the second *recording* is refused, after the patient already got two emails. The fix is a claim that one session wins before the send. Options for 4b: (a) a new status `sending` (migration, CHECK and tables change): `approved -> sending` is the claim, then `sent` or `failed`; (b) allow `failed -> approved` so a resend is a claim too (then the "may have been sent" error is cleared on resend, which the audit log records; this reverses the "error clearing is settled" note above for that one move). No `expected=` argument is needed: the `status IN (sources)` check already compares against the status at update time.
- **Retention concurrency (checked 2026-10-23):** two retention runs at once handle each lead exactly once (`FOR UPDATE` on each batch); test `test_two_retention_runs_at_once_handle_each_lead_exactly_once` fails 5 of 5 times when the lock is removed. A write during a run is covered by the lock: a plain "is it anonymized?" read did NOT wait for the batch (probe: a reply with patient text was stored on a lead retention had just blanked), so the check now takes `FOR SHARE` and waits (tests `test_add_reply_during_a_retention_batch_waits_then_refuses` and two more; they fail without the lock). The retention sweep was removed because of this. Residual: someone writing personal text with raw SQL, bypassing the repository, is no longer cleaned up automatically. Still open: no `lock_timeout` (parked F7).
- **Scheduling retention is not built yet.** `scripts/run_retention.py` is the unit a scheduler runs; nothing schedules it. Recommended: daily (the job is idempotent and cheap), because the promise to a client is "personal data is gone after 90 days"; every 5 days would keep data up to 95. Pick the scheduler with the deployment layer (cron or a Docker job on the VPS, Windows Task Scheduler for the local demo, or an n8n schedule trigger in layers 5-6) and have it alert when the job fails or the count looks wrong.
- **Staff login stays our own table for now (decided).** 4c keeps login in one module so OIDC can replace it later (PROJECT.md Decisions log). `staff_users` stays even with external auth: `approved_by` and the audit log point at it.
- **Lead status after routes 1 and 2 was unspecified; provisional answer now in the code.** Routes 1 (classifier failed) and 2 (language not DE/EN) send only the acknowledgment and create no draft, so the spec's flow `acknowledged -> awaiting_approval` did not cover them. The transition table now lets them stay `acknowledged` (flagged `needs_manual_review`) and later go `acknowledged -> done`; 4b confirms or changes it. 4d's dashboard must list these leads (flagged) either way. Route 4 (routine during opening hours) and a downgraded auto-reply behave like route 3 (acknowledgment plus draft).
- **Lead status vs reply kind:** `leads.status` is one value for the lead; `replies.kind` (`auto_reply`, `acknowledgment`, `draft`) is what was sent or drafted. A sensitive lead has two replies (the acknowledgment sent at once, the draft waiting).
- **Indexes with no query yet:** `ix_leads_status` and the status half of `ix_replies_lead_id_status` are for the 4d dashboard queues. Keep them if 4d uses them, otherwise cut them.
- **4b flow to build against** (from the spec, not yet run): form -> API validates -> `create_lead_if_new` (duplicate: stop) -> pipeline -> `set_classification` and `add_reply` -> acknowledgment sent at once -> staff approve in 4d -> sender delivers (Mailpit in development) -> retention after 90 days. `add_reply` and `set_classification` raise `ValueError` for an anonymized lead; 4b must handle it.
- **Mailpit** is the fake inbox for development (SMTP 1025, web 8025). Production changes only the SMTP settings in `.env`.

### Parked: 4a small items
- ~~"sent is final" is not enforced~~ fixed 2026-10-23: transition tables plus a conditional UPDATE in `set_reply_status` / `set_lead_status` (see "Carry into the 4b/4c/4d plans"). The error-clearing question is settled too (see 4a notes).
- **One commit per repository method** vs spec section 14 row 13 (audit row in the same transaction): decided 2026-10-24 (Tariq): the commit stays in the repository, so 4c writes the audit row inside the repository methods (approve, edit, reject, resend), in the same transaction as the change. A unit of work (the service commits once) was the alternative.
- **Retention can blank a pending draft.** (Since the attack fix wave `add_reply` and `set_classification` also raise `ValueError` for an anonymized lead; `set_reply_status` stays allowed for status moves, but its `error` text is dropped for an anonymized lead.) The 4b sender and the 4d approve action must refuse leads with `anonymized_at` set (or an empty email).
- **`run_retention.py` uses `Session(engine)`, not `make_session_factory`.** 4b must use `make_session_factory`, one session per request.
- Dedup lower-cases the email but does not strip it (leave to 4b form validation). `set_classification` stores the model's raw language (cut to the column width), not the normalised `decision.language`.
- `DATABASE_URL_TEST` could point at the dev database and the tests run `drop_all`: consider a guard that the database name ends in `_test`. Two checkouts running the suite at once share `leads_test`. Length-CHECK drift is not caught by `compare_metadata`.
- ~~Retention updates with `IN (ids)`~~ fixed in the attack fix wave: batches of `RETENTION_BATCH_SIZE` (5000), one transaction each.
- The 120 s duplicate window has no upper bound on lead timestamps by design (a lead stamped later than `now` still counts); revisit if clocks can drift.
- `run_retention` does not call `engine.dispose()`; harmless for a one-shot script, add it if the script grows.

### Parked: redactor hardening (decided 2026-10-22: not now)
Done already: emails, phones, cards, IBANs, Versichertennummer become `[NUMBER]`; dates in d.m.yyyy, d/m/yyyy, d-m-yyyy, yyyy-m-d and times are kept.
Known gaps, in order of value, if we ever come back to them:
1. Scrub the form's own name/email/phone fields from the message text (deterministic; fixes signatures like "Mit Grüßen, Anna Müller").
2. Birth dates after a keyword (`geboren`, `geb.`, `Geburtsdatum`, `born`, `DOB`).
3. Street plus house number (`...straße 12`).
Not fixable by redaction: names of other people ("mein Sohn Max"), and the health content itself (Art. 9), which is why a real deployment needs the AVV with Anthropic. Every new pattern needs a "must not redact" test too. This is orientation, not legal advice.

### Attack report 2026-10-23 (fresh agent, fakes only): what was fixed and what is parked
Fixed with regression tests (`tests/test_attack_findings.py`): name field sanitised (`prompting.sanitize_name`); guardrails now check links, phone/email/ID numbers, placeholders and length on the FINAL text, for auto-replies and safe-info; safe-info that says "None ..."/"Keine" is dropped; keyword net widened (Zahnweh, Zahnfleischbluten, Eiter, gebrochen, toothache, ...); non-DE/EN leads always get emergency info and `needs_manual_review`.

Parked, with reasons:
- **Layer 4 requirements (not fixable in layers 1-3):**
  - The API handler must wrap `process_inquiry`: any exception (not only LLMError) sends the route-1 acknowledgment and flags the lead. Validate `AUTO_REPLY_MODE` at start-up.
  - Send the fixed acknowledgment FIRST, then do the AI calls, with an overall per-inquiry deadline. Today the "instant" acknowledgment waits for the slowest AI call (worst case about 13 minutes with 4 attempts per call).
  - Duplicates and rate limits per sender, and never send to an address that did not come from the form field the visitor controls without limits (a stranger's address plus attacker text is a mail-bomb vector).
- **Redactor leaks (add to "Parked: redactor hardening"):** `0511 12.34.56`, `0511 12:34 567` and `0511 9.12.2026` stay visible because the date/time preservation hides parts of a phone number; an en dash `0511–1234567` leaves `0511`; non-ASCII email names leave part of the name (`mü[EMAIL]`); obfuscated emails ("mueller at gmail dot com") are not caught.
- **Guardrail gaps:** prices in words or other forms (`achtzig Euro`, `$80`, `CHF 80`, `99.- Euro`), free-of-charge promises ("kostenlos"), invisible characters, more diagnosis stems (`entzündet`, `Parodontose`, `inflamed`), and the false alarm on `Kariesprophylaxe`.
- **Zero-width-only messages** pass the empty check and cost a classifier call; **cost accounting** ignores failed attempts and unpriced fallback models (matters once budget caps rely on `cost_usd`).
- **24 and 31 December** count as open; ask the practice and put the answer into the FAQ and `practice.yaml`.

## Open decisions for Tariq
- [ ] Monthly API budget (suggested $20 while learning); record in PROJECT.md Decisions log.
- [ ] Keep `.agents/` and `skills-lock.json` (grilling skills installed for another tool)?
- [ ] Add a "what enters the dev setup?" step to the definition of done?

### Attack probe of layer 4a, 2026-10-23 (fresh agent): fixed and parked
Fixed with regression tests (`tests/db/`): F3 dedup key joined email and message with a newline, so two different patients could collide (separator is now NUL, computed after cleaning); F1 retention used one `IN (ids)` list (65535 bind-parameter limit), now batched; F2 personal data could re-enter an anonymized lead (guards in `add_reply` / `set_classification`; the retention sweep first added for this was later replaced by a `FOR SHARE` lock on the check and by dropping the error text in `set_reply_status`); F6a NUL in `language`.

Parked (all from the 2026-10-23 attack probe):
- **F4 stale identity map:** fixed 2026-10-24 for `get_lead` and `replies_for` (`populate_existing`). Still true for objects a caller keeps and for `_require`; 4b/4d: one short session per request.
- ~~**F5 no reply state machine**~~ fixed 2026-10-23 together with "sent is final": `sent` -> `sent` is refused (`sent_at` no longer moves) and two sessions cannot both mark a reply `SENT`.
- **F6b no length cap** on `ai_reason`, reply `body`, `ai_body` and `error` (50 MB accepted); lone surrogates raise `UnicodeEncodeError`.
- **F7 no lock or statement timeout anywhere** (retention and writers wait as long as another transaction holds a row; `make_engine` sets none): add `lock_timeout` and `idle_in_transaction_session_timeout` in `make_engine` (for example `-c lock_timeout=5s -c idle_in_transaction_session_timeout=60s`) and consider `SKIP LOCKED` for retention. The part about `get_lead` leaving a transaction open is fixed (2026-10-24).
- **F8 read methods did not roll back:** fixed 2026-10-24 (commit/rollback like the writers). Still open: wrong-type ids raise `DataError` / `ProgrammingError`, not `ValueError`; belongs to 4b input validation.
- **F9 pre-commit gate:** `git mv code.py code.md` is treated as docs-only (add `--no-renames`); the `.env` block does not cover `.envrc`, `prod.env`, `.ENV`.
- **F10 misc:** channel not validated; empty name/email/message accepted; `anonymize_older_than` accepts a future cutoff (wipes everything); no per-lead erasure method (DSGVO Art. 17); `audit_log.details` is not blanked by retention; `alembic downgrade base` drops all data; docker-compose publishes 5433 on all interfaces with password `leads` (dev only, bind to 127.0.0.1 before any shared host); dedup is bypassable by spelling variants until 4b rate limits.

### Attack probe 2 of layer 4a: concurrency and edge cases, 2026-10-24 (fresh agent, `leads_probe` database)
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
