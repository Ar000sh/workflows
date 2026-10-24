# Review & Next Steps (Tariq's checklist)

> A1 review file; each project keeps its own REVIEW.md next to its README. Central place for work that needs **Tariq's review or decision**. Tick items off, add notes. Update it whenever a step finishes or new review work appears.
> Last updated: 2026-10-24 (layer 4b built; its review guide is the section "Layer 4b: intake API and sender")

## Where we are
- A1 layers 1–3 (core logic) built; the mocked unit tests pass; Python pinned to 3.12 (`.python-version`).
- First live runs on 2026-10-22 (Haiku 4.5 classifier, Sonnet 5.5 drafter, effort low):
  - Classifier: category 44/45, risk 45/45, language 45/45, sensitive→routine **0**, injection failures **0**, cost $0.057.
  - Drafter: 18 auto-replies, 0 guardrail rejections, 0 API errors, cost $0.445 (about $0.01 per inquiry).
- Layer 4a exists (database, repository, retention), merged into `main`.
- Layer 4b exists on branch `a1-4b-intake-sender` (intake API, email sender, sweep job); your review is open, see "Layer 4b" below. No staff login, audit log, dashboard or n8n yet.

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
- [x] 4b intake API and sender (built 2026-10-24; your review is open, see "Layer 4b" below).
- [ ] 4c staff login and audit log, 4d HTMX dashboard, 4e demo site and end-to-end. See spec section 9.
- [x] Form/API validation (found by the edge-case probe): message, name, email and phone capped at the API (`LeadIn`, limits in `limits.py`; the pipeline still sends at most 5000 characters to the AI), the name is sanitised before it enters a reply (`sanitize_name`). Still open for 4d: escape everything on display (Jinja autoescape stays ON).
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
2. The layer 1-3 pipeline classifies and drafts. The result goes back through the Repository (classification on the lead, draft as a reply with status `drafted`).
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

### 4a notes: to decide or know (2026-10-23)
- [x] **Error is cleared on every status change: no longer a problem (2026-10-23).** `set_reply_status` still sets `error` to the given value or None each time, but with the transition tables the risky move (`failed` -> `approved`, which dropped the "may have been sent" warning) no longer exists: from `failed` a reply can only be claimed again (`failed -> sending` since 4b; the claim, then `sent` clears the error, and the audit log in 4c records the resend). Nothing to decide; keep as is.
- [x] **Retention blanks `leads.ai_reason` and `replies.error`** (done in the final-review fixes; `error` may hold a recipient address, `ai_reason` is free text about the patient).
- [ ] **Length limits are CHECK constraints** on text columns (`char_length`), not `VARCHAR(n)`. Overflow raises `IntegrityError`; since 4b the API model `LeadIn` checks first (clear 422), using the same numbers from `limits.py`.
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

## Layer 4b: intake API and sender (built 2026-10-24)
Spec: `docs/superpowers/specs/2026-10-24-a1-layer-4b-intake-and-sender-design.md` (section 4 holds every decision, section 8 the failure modes). Plan: `docs/superpowers/plans/2026-10-24-a1-layer-4b-intake-and-sender.md`. Branch `a1-4b-intake-sender`, commits `b1fc14b` to the docs commit at the end (`git log --oneline dc9846f..a1-4b-intake-sender`). The attack pass (fresh agent, concurrency as focus) is still to run in the main session.

### What 4b is and how its pieces fit
**The problem it solves.** After 4a the clinic had memory but no door and no voice: nothing could receive a form post and nothing could send an email. 4b adds both, with the three promises a practice needs: the patient always gets an answer within seconds (even when the AI is slow, down or over budget), no email ever goes out twice, and nobody can use the form to flood someone's inbox.

```
 website form
     │ POST /leads (JSON)
     ▼
 api/app.py  _Guard: 413 over MAX_BODY_BYTES, generic 503/500 · LeadIn (api/schemas.py, limits.py): 422
     │ validated LeadIn                                    always answers 202 {"status": "received"}
     ▼
 IntakeService.receive ──► Repository.create_lead_if_new (duplicate: stop)
     │                    └► count_leads_for_mailbox_since → over the limit: suppress_lead (no email, no AI)
     │ background task (after the answer)
     ▼
 IntakeService.process ── global AI cap / no free slot / deadline / any exception ──► ReplyService.acknowledge_with_fallback
     │ pipeline (layers 1-3) in a worker thread, 45 s deadline                          (fixed DE+EN text, flagged)
     ▼
 write result: set_classification · acknowledge_lead (first reply, one winner) · add_reply (staff draft "drafted")
     │ first reply ids
     ▼
 ReplyService.send ── claim: set_reply_status(SENDING), committed ──► SmtpSender.send (no transaction open)
     │                                                                  │ SendResult: ACCEPTED / FAILED / TIMEOUT
     ▼                                                                  ▼
 record: sent · failed (class name only) · stays "sending" (unknown)    Mailpit (dev) / real SMTP (prod)

 scheduler ──► scripts/run_sweep.py ──► ReplyService.sweep: stuck acknowledgments (resend, capped; also
                                         ones a broken sweep left "failed"), stuck auto-replies (flag only),
                                         first replies left "drafted" (send), leads with no first reply (fallback);
                                         each item on its own (one that raises: logged, flagged, counted, skipped)
```

**The pieces, one line each:**
- **`limits.py`**: every length limit once (name 200, email 254, phone 50, message 10,000), used by `LeadIn` and by the database CHECKs; `mailbox_key()` normalizes an address for the per-email limit.
- **`api/schemas.py` (`LeadIn`)**: the only place form input is checked (consent, email format, limits, NUL and whitespace stripping, empty name/message refused).
- **`api/app.py` (`create_app`)**: `POST /leads` and `GET /health`; the body-size guard, generic error answers, CORS allowlist; the same 202 answer for a new, duplicate or suppressed lead. **`api/main.py`** wires the real pieces lazily (importing it needs no database or key).
- **`services/intake.py` (`IntakeService`)**: the business rules of intake: duplicate, per-email limit, global AI cap, pipeline slots, deadline, writing the result, the fallback.
- **`services/replies.py` (`ReplyService`)**: sending exactly once (claim before send), recording the result, fixing the lead status after a first reply, and the sweep.
- **`sender.py` (`SmtpSender`)**: one email over SMTP with a typed result and the retry policy (spec section 7).
- **`repository.py`**: gained `acknowledge_lead`, `suppress_lead`, `flag_for_review`, `fail_stuck_reply`, the count and sweep queries, and lost its input validation (now in `LeadIn`).
- **Migration `e93e6e744ccc_sending_claim.py`**: reply status `sending`, `replies.claimed_at`, `replies.send_attempts`, `leads.send_suppressed`, `leads.mailbox_key` (with its index).
- **`scripts/run_sweep.py`**: the unit a scheduler runs, same shape as `run_retention.py`; prints every count and exits 1 when an item raised.

**Concepts practiced:** service layer (one rule, one layer); claim before send, also called the outbox pattern (the database decides who may send before the email leaves, because an email cannot be taken back); compare-and-set (a conditional UPDATE that only one session wins); a typed result instead of exceptions (three outcomes need three different recordings); background work after the HTTP answer; an idempotent scheduled job; rate limiting on a normalized key.

**Every public method (as built; signatures in the code):**

| Class | Method | What it does |
|---|---|---|
| `Repository` | `create_lead_if_new(*, name, email, phone, message, consent_at, now, mailbox_key, channel="web_form") -> (Lead, created)` | Saves a lead unless the same email + message came in the last 2 minutes (advisory lock makes it atomic) |
| `Repository` | `get_lead(lead_id)` / `get_reply(reply_id)` | Fresh read of one row (reloaded, transaction ended); `None` if unknown |
| `Repository` | `set_classification(lead_id, *, classification, decision)` | Stores the AI's classification and the router's flags, only while the lead is `new` (a late result gets `InvalidTransition`) |
| `Repository` | `set_lead_status(lead_id, status)` | Moves the lead along `LEAD_TRANSITIONS` (one conditional UPDATE) |
| `Repository` | `acknowledge_lead(lead_id, *, kind, ai_body, body, now, needs_manual_review=False) -> Reply` | Writes a lead's first reply (acknowledgment or auto-reply) and the status move in one transaction under a row lock; exactly one writer wins; refuses suppressed and anonymized leads |
| `Repository` | `suppress_lead(lead_id)` | Per-email limit tripped: `new -> acknowledged`, flagged, `send_suppressed`, no reply |
| `Repository` | `flag_for_review(lead_id)` | Sets `needs_manual_review` without touching the status |
| `Repository` | `add_reply(lead_id, *, kind, ai_body, body, status, now, edited=None)` | Stores a reply (4b uses it for staff drafts, status `drafted`) |
| `Repository` | `set_reply_status(reply_id, status, *, now, error=None, approved_by=None, edited=None)` | Moves a reply along `REPLY_TRANSITIONS`; `-> sending` is the claim (stamps `claimed_at`, counts `send_attempts`) |
| `Repository` | `fail_stuck_reply(reply_id, *, claimed_before, error)` | Sweep: gives up a claim older than `claimed_before` (`sending -> failed`), re-checking both conditions in one UPDATE |
| `Repository` | `replies_for(lead_id)` | All replies of a lead, oldest first |
| `Repository` | `count_leads_for_mailbox_since(mailbox_key, since)` / `count_leads_since(since)` | Counts for the per-email limit and the global AI cap |
| `Repository` | `stuck_sending_replies(*, kind, claimed_before, max_attempts)` | Sweep part 1: replies of one kind stuck in `sending` |
| `Repository` | `failed_stuck_replies(*, kind, error, claimed_before, max_attempts)` | Sweep part 1: replies the sweep itself moved to `failed` (error = `STUCK_ERROR`) but could not claim again; same age rule and attempt cap |
| `Repository` | `unflagged_leads_with_stuck_replies(*, kind, claimed_before)` | Leads whose reply of one kind is stuck in `sending` and that are not flagged yet (the sweep flags stuck auto-replies, never resends them) |
| `Repository` | `stale_drafted_first_replies(*, created_before)` | Sweep part 1b: acknowledgments and auto-replies written but never claimed |
| `Repository` | `leads_without_first_reply(*, created_before)` | Sweep part 2: leads still waiting for a first reply, not suppressed, not anonymized |
| `Repository` | `anonymize_older_than(*, cutoff, now, batch_size=5000)` | DSGVO retention in batches (4a, now also blanks `mailbox_key`) |
| `ReplyService` | `send(reply_id) -> SendOutcome` | Claims, sends, records; `SENT`, `FAILED`, `IN_DOUBT` (stays `sending`), `ALREADY_HANDLED` (lost the claim or not sendable now), `REFUSED` (no address, or a draft not approved) |
| `ReplyService` | `acknowledge_with_fallback(lead_id) -> bool` | Writes and sends the fixed DE+EN acknowledgment with emergency numbers, flags the lead; `False` if the lead already has a first reply or is suppressed |
| `ReplyService` | `sweep() -> SweepResult` | The scheduled clean-up; returns `retried_acknowledgments`, `fallback_acknowledgments`, `left_for_staff`, `sent_stale_drafted`, `errors` (items that raised: logged, lead flagged, skipped), `flagged_stuck_auto_replies` |
| `IntakeService` | `receive(lead: LeadIn) -> Received or None` | Saves the lead and applies the per-email limit while the form waits; `None` = duplicate; `Received(lead_id, run_pipeline)` |
| `IntakeService` | `process(lead_id) -> None` | The background part: AI cap, slot, pipeline under the deadline, writing the result, sending first replies; never raises |
| `IntakeService` | `shutdown()` | Waits for running pipelines and stops the worker threads (app shutdown) |
| `SmtpSender` | `send(*, to, subject, body) -> SendResult` (constructor takes `local_hostname`, default `localhost`) | One email: `ACCEPTED`, `FAILED` (after 3 retries on refusals; detail = class name) or `TIMEOUT` (never retried); refuses CR/LF in recipient or subject |

Other entry points: `create_app(*, intake, max_body_bytes, cors_origins)` and `build_app()` (`api/main.py`, `app` for uvicorn), `scripts/run_sweep.main()`, `limits.mailbox_key(email)`, `LeadIn`.

### 4b: your review, in this order (about 2 hours; paths under `track-a-dental/a1-lead-responder/`)
- [ ] **1. Run it (15 min).** README "Intake and sending (layer 4b)": `docker compose up -d postgres mailpit`, `alembic upgrade head`, `uv run pytest tests/db/test_end_to_end.py` (real SMTP into Mailpit, fake AI), then open http://localhost:8025 and look at the mails the tests sent. Optional, costs about $0.01: start `uvicorn lead_responder.api.main:app` and post the README's `curl` lead. Run `uv run python -m lead_responder.scripts.run_sweep` once.
- [ ] **2. The status tables (15 min).** `db/models.py` `REPLY_TRANSITIONS` and the migration `alembic/versions/e93e6e744ccc_sending_claim.py`; compare with the reply status flow in A1 spec section 5. Ask: is `sending` = unknown and `failed` = known not sent clear enough for staff?
- [ ] **3. Sending (30 min).** `sender.py` (how an error's stage decides FAILED vs TIMEOUT), then `services/replies.py` `send` and `_send` (steps 1-6 in the module docstring). Test to read: `tests/db/test_reply_service.py::test_two_sends_of_one_reply_at_once_send_one_email`.
- [ ] **4. Intake (30 min).** `services/intake.py` (`receive`, `process`, `_run_pipeline`, `_write_result`), then `repository.py` `acknowledge_lead`. Tests: `tests/db/test_repository_acknowledge.py::test_two_sessions_acknowledging_one_lead_exactly_one_wins`, `tests/db/test_intake_service.py`.
- [ ] **5. The API (15 min).** `api/app.py` (`_Guard`, the 422 handler that hides the submitted values) and `api/schemas.py`. Tests: `tests/db/test_api.py`.
- [ ] **6. The sweep (15 min).** `ReplyService.sweep` and the five repository queries (`stuck_sending_replies`, `failed_stuck_replies`, `unflagged_leads_with_stuck_replies`, `stale_drafted_first_replies`, `leads_without_first_reply`); tests `tests/db/test_sweep.py` (the three overlapping-sweeps tests).
- [ ] **7. Logs (5 min).** `tests/db/test_logging.py`, and the one change it forced: `pipeline._Run.event` logs the event name only.
- [ ] **8. Decide the items in "4b: to decide or confirm"** below, skim the build rulings and the Parked list, then decide on the merge (after the attack pass).

### 4b: to decide or confirm
- [ ] **Draft guardrail warnings are not stored in 4b.** The pipeline computes them, but `IntakeService` stores only the draft text; 4d recomputes the warnings when staff open a draft (and 4c stores the ones shown at approval in `audit_log.details`). Alternative: a column on `replies`.
- [ ] **`failed -> sent` and `failed -> failed` were removed from `REPLY_TRANSITIONS`.** The only way back from `failed` is a new claim (`failed -> sending`). A staff "it did arrive after all" for a `failed` reply is therefore not possible; for a stuck `sending` reply it is (`sending -> sent`).
- [ ] **Lead status for routes 1 and 2 confirmed:** they stay `acknowledged`, flagged, until staff mark them `done` (4b spec section 4, "Mine"; object if you disagree).
- [ ] **Auth errors and permanent 5xx are retried 3 times** (A1 spec section 7 says so; ruling 6). A wrong SMTP password then costs 4 logins per email, and some providers lock an account after repeated failures. Keep, or fail at once on auth errors?
- [ ] **The limits' numbers are guesses** (3 per mailbox per hour, 10 per day, 60 AI leads per hour, 10 parallel pipelines, 45 s deadline, 10 min stuck, 3 attempts, 5 min no-reply). Tune them with the practice before going live.
- [ ] **The pre-commit gate is close to 90 s.** The full suite ran 35-60 s during 4b, once 91 s, 43 s at the end (530 tests). The rule says ask you: keep the 90 s limit, raise it, or run only the relevant tests in the gate? Never bypassed so far.
- [ ] **Scheduler for `run_sweep` and `run_retention`:** nothing runs them yet. Choose with the deployment layer (cron or a Docker job on the VPS, Task Scheduler for a local demo, an n8n schedule trigger in layers 5-6) and add an alert when a job fails. Until then a lead whose processing died gets its fallback acknowledgment only when someone runs the sweep.

### Build rulings (decisions Claude took on your behalf during the build; each can be undone)
From the build ledger (`.superpowers/sdd/2026-10-24-a1-layer-4b-intake-and-sender/progress.md`).
- [ ] **Ruling 1:** `ReplyService.send` on a staff draft still `drafted` returns `REFUSED` and sends nothing; `ALREADY_HANDLED` is only for losing the claim or a reply already sent, rejected or in flight (one outcome per cause). *If wrong:* a test name and an assertion change.
- [ ] **Ruling 2:** the sweep's `sending -> failed` step stores the fixed error `stuck: retried by sweep` (no personal data). *If wrong:* cosmetic.
- [ ] **Ruling 3:** `receive` does only the cheap local work (save, per-email limit) and returns `Received(lead_id, run_pipeline)` or `None`; the global AI cap and the pipeline-slot check run at the start of `process` in the background, which then sends the fixed acknowledgment. Differs from the plan text, which put both checks in `receive` (the form would have waited for a send). *If wrong:* the two checks move between the two methods and one test moves with them.
- [ ] **Ruling 4:** `acknowledge_lead` refuses a `send_suppressed` lead under the lock it already holds, so "a suppressed lead gets no email" holds whoever asks. *If wrong:* a redundant check. Residual risk parked below (intake crash between save and suppress).
- [ ] **Ruling 5:** `SmtpSender` judges an error by its stage: before `send_message` (connect, EHLO, STARTTLS, login) a refusal, disconnect or auth error is FAILED and retried; a timeout at any stage is TIMEOUT at once, never retried (also a connect timeout, which keeps a draft waiting for staff); during `send_message` a server refusal (4xx/5xx, recipients refused) is FAILED and retried, a dropped connection or other OSError is TIMEOUT. *If wrong:* more in-doubt replies than necessary, never a duplicate.
- [ ] **Ruling 6:** auth errors and permanent 5xx are still retried 3 times, as A1 spec section 7 and the plan's test require (see "to decide" above). *If wrong:* provider lockout risk.
- [ ] **Ruling 7:** the send rule by kind is checked before the no-address refusal, and a `drafted` staff draft is left untouched on refusal (moving it to `failed` would have made it sendable without approval); `send` returns `ALREADY_HANDLED` for a reply in `sending` (only the claim's owner may move its own claim to `failed`). *If wrong:* an unapproved draft could become sendable, so keep this one.
- [ ] **Ruling 8:** two sweep weaknesses are parked, not fixed (one poison item can stop a sweep run; a crash between `fail_stuck_reply` and the re-claim leaves an acknowledgment `failed`). *If wrong:* one lost acknowledgment in a tiny crash window, seen by staff only through the dashboard. The attack pass or the final review may decide to fix them before merge. **Superseded by ruling 10: both FIXED.**
- [ ] **Ruling 10 (final review, I-1):** the sweep runs every item inside its own try/except (class name and id logged, lead flagged best effort, counted in `SweepResult.errors`, the run goes on; `run_sweep` exits 1), and part 1 also resends acknowledgments left `failed` with the sweep's own error `stuck: retried by sweep`, below `MAX_SEND_ATTEMPTS` and claimed before the same cutoff. Plus: stuck auto-replies flag their lead (never resent), `SmtpSender` passes `local_hostname`. *If wrong:* a little extra sweep code; the happy path is unchanged. Small leftover: if two sweeps both read such a `failed` acknowledgment and the first one's resend ends `failed` with a provider error, the second may still claim and try it once more (known not sent before, so never a duplicate email; the attempt cap can be exceeded by one in this case).
- [ ] **Ruling 9:** both a local fix (`process` always tries to send a first reply it has written, even when a later step raised) and a sweep step 1b (first replies still `drafted` after `NO_REPLY_SWEEP_MINUTES` are sent through `send`, which claims; staff drafts never) so that no patient is left without a reply. *If wrong:* one extra query and its tests.

### Go-live gate (must be true before a real practice uses the form)
- [ ] **The sweep (and retention) are scheduled.** Without the sweep the "never left without a reply" safety net does not run at all: a deploy or restart kills the background tasks of the leads in flight, and only the sweep answers them. Retention is a DSGVO duty. Alert on `run_sweep` exit code 1.
- [ ] **Set `SMTP_LOCAL_HOSTNAME`** to the sending host's real FQDN (or the practice's mail domain). The default `localhost` is for development, and some mail servers reject or score down a non-FQDN HELO name.
- [ ] **The parked global email cap, per-IP limits and a captcha are done** before the form is on a public site (Parked: 4b, first item; A1 spec section 12).
- [ ] **Accept or change the per-mailbox cap's blind spot:** a real patient's 4th post within the hour is saved but gets no email, and only a staff flag shows it; the 4d dashboard that would show the flag is not built yet.
- [ ] **Threads under an SMTP slowdown:** fallback and acknowledgment sends run in the web server's pool of about 40 worker threads; a flood of posts while SMTP is slow (up to 10 s timeout and 7 s of retry waits per mail) can queue ordinary posts behind them.
- [ ] **Non-ASCII (IDN) addresses:** an address like `anna@müller.de` passes the form but the send ends in doubt (`sending`) and is retried pointlessly up to the cap. Decide: refuse non-ASCII addresses in `LeadIn`, or send with SMTPUTF8.

### Carry into the 4c/4d plans (from the 4b build)
- **4d must provide staff actions** on top of `set_reply_status` (each with auth and an audit row from 4c): resolve a stuck `sending` reply (`sending -> sent` "confirmed delivered" or `sending -> failed` "not delivered"), resend a `failed` reply (through `ReplyService.send`, which claims), approve and reject drafts. Show `InvalidTransition` / `ALREADY_HANDLED` as "already handled".
- **4d must refuse** leads with `anonymized_at` set or an empty email for any send action (`ReplyService.send` already returns `REFUSED`).
- **4d lists:** leads flagged `needs_manual_review`, `send_suppressed` leads (over the per-email limit: the patient got nothing), replies `failed`, replies stuck in `sending` (drafts and auto-replies wait there for staff; acknowledgments at `MAX_SEND_ATTEMPTS`).
- **4d recomputes draft guardrail warnings** when staff open a draft (they are not stored in 4b); 4c stores the warnings shown at approval in `audit_log.details`.
- **Scheduler and alerting** for `run_sweep` (every few minutes) and `run_retention` (daily): deployment layer.

### Parked: 4b
- **Global cap on emails sent per hour (4b probe P4, parked 2026-10-24, Tariq).** The per-mailbox cap does not stop one visitor from sending each of many different victims one fixed acknowledgment (a low-grade mail relay for the acknowledgment text and the sanitized name). Idea: `MAX_EMAILS_PER_HOUR` (default about 120); above it the lead is saved, flagged and `send_suppressed`, no email. Take it up before the form is on a real public site, together with the per-IP limits and captcha deferred in A1 section 12.
- **Cancellable LLM calls.** A pipeline run that outlives its 45 s deadline keeps running and spends its few cents; making `shared/llm.py` cancellable would stop that (4b spec, section 4).
- **FIXED (final 4b fix wave, ruling 10): Sweep has no per-item catch-all:** one poison item (a reply or lead whose processing raises) stopped the run, on every run, starving the rest. Now every item has its own try/except, `SweepResult.errors` counts them and `run_sweep` exits 1.
- **FIXED (final 4b fix wave, ruling 10): Crash window in sweep part 1:** between `fail_stuck_reply`'s commit and the re-claim the acknowledgment was `failed`, maybe unflagged, and never retried. Now the next sweep resends acknowledgments `failed` with `stuck: retried by sweep` (below `MAX_SEND_ATTEMPTS`), and a raising re-send flags the lead.
- **Intake crash between saving a lead and `suppress_lead`** (or the count failing): the lead stays unsuppressed and the sweep later sends ONE fallback acknowledgment past the per-email limit. Proper fix: count and suppress inside the create transaction.
- **`process()` has no guard at its top** for suppressed or not-`new` leads: a buggy caller or a late task could spend API money (the writes are still refused).
- **No index on `replies(status, created_at)`** for `stale_drafted_first_replies` and the first-reply queries; fine at practice volume.
- **Retention leaves an anonymized lead's `drafted` first reply `drafted` forever:** harmless (it is never sent, the sweep query skips anonymized leads), but the row is never closed.
- **SMTP details:** `SMTPNotSupportedError` raised inside `send_message` is classed in doubt (arguably failed). No `Date` or `Message-ID` headers (a Message-ID derived from the reply id would let receivers drop exact duplicates from a sweep retry). FIXED (final 4b fix wave): smtplib called `socket.getfqdn()` for EHLO on every connection (about 1 s per email on this Windows machine); `SmtpSender` now passes `local_hostname` (`SMTP_LOCAL_HOSTNAME`, default `localhost`).
- **`failed_stuck_replies` does not filter anonymized or empty-email leads:** such a row (`STUCK_ERROR` is written on purpose even for an anonymized lead) is re-read and refused on every sweep with one warning log line, no email, no error counted: harmless noise. Fix idea: join `Lead` and require `anonymized_at IS NULL` and a non-empty email.
- **The claim `failed -> sending` does not re-check the attempt cap or the error text,** so for a sweep resend the cap can be reached plus one (two sweeps reading the same failed-by-sweep acknowledgment, first resend refused). Never a duplicate email. Fix idea: make the claim's WHERE re-check `error = STUCK_ERROR AND send_attempts < MAX_SEND_ATTEMPTS` for sweep resends.
- **psycopg `connect_timeout` is unset:** with the database unreachable mid-sweep each item may wait for the OS TCP timeout twice. Decide with the deployment layer.
- **Subject language** comes from `lead.language` at send time, not stored on the reply. A whitespace-only phone is stored as `""`, not `None`.
- **API hardening:** `_BodyTooLarge` derives from `BaseException` (works, but depends on FastAPI's body-reading internals; a Starlette `HTTPException(413)` would be sturdier); a connection-pool timeout should answer 503 (today 500); reject `CORS_ORIGINS=*`; a lower bound for `MAX_BODY_BYTES` (a tiny value refuses every lead).
- **`shared/llm.py` log lines** include the API error message and `repr` of exceptions (`LLM attempt failed: ...`). Anthropic's errors do not normally quote the prompt, and the prompt is the redacted message, but for a strict "ids only" rule log the status code and class name there too (it is shared code, so it was left for a separate change).

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
