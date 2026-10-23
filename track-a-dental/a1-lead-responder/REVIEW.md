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

### 4a notes: to decide or know (2026-10-23)
- [ ] **Error is cleared on every status change.** `set_reply_status` sets `error` to the given value or None each time, so a `failed` then `approved` retry loses the "may have been sent" warning. Decide in 4b/4c: clear only on `sent`, or only when `error` is passed.
- [x] **Retention blanks `leads.ai_reason` and `replies.error`** (done in the final-review fixes; `error` may hold a recipient address, `ai_reason` is free text about the patient).
- [ ] **Length limits are CHECK constraints** on text columns (`char_length`), not `VARCHAR(n)`. Overflow raises `IntegrityError`; the repository checks first and raises a clear `ValueError`.
- [ ] **Sessions:** `make_session_factory` uses `expire_on_commit=False`. A caller whose Session keeps the default (`True`) gets expired objects back from the repository (attributes reload on access).
- [ ] **Duplicates are only suppressed, not blocked:** the advisory lock plus the 2-minute window stops identical simultaneous or quick repeat submissions; it is not rate limiting.

### Parked: 4a small items
- **"sent is final" is not enforced:** `set_reply_status` allows any transition, including `sent` -> `failed`. Spec section 7 and section 14 rows 11 and 16 need a compare-and-set (`expected=`) or a transition table. Decide it together with the error-clearing question above.
- **One commit per repository method** vs spec section 14 row 13 (audit row in the same transaction): 4c must add audit writes inside the repository methods, or a no-commit / unit-of-work variant.
- **Retention can blank a pending draft.** (Since the attack fix wave `add_reply` and `set_classification` also raise `ValueError` for an anonymized lead; `set_reply_status` stays allowed and its `error` is swept on the next retention run.) The 4b sender and the 4d approve action must refuse leads with `anonymized_at` set (or an empty email).
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
Fixed with regression tests (`tests/db/`): F3 dedup key joined email and message with a newline, so two different patients could collide (separator is now NUL, computed after cleaning); F1 retention used one `IN (ids)` list (65535 bind-parameter limit), now batched; F2 personal data could re-enter an anonymized lead (guards in `add_reply` / `set_classification`, plus a retention sweep of leftovers); F6a NUL in `language`.

Parked (all from the 2026-10-23 attack probe):
- **F4 stale identity map:** `expire_on_commit=False` plus `session.get` returns cached objects, so a long-lived session can show PII after retention and drop writes. 4b: short sessions per request, `populate_existing=True` in lookups.
- **F5 no reply state machine** (see above): also `sent` -> `sent` moves `sent_at`, and two sessions can both mark a reply `SENT`.
- **F6b no length cap** on `ai_reason`, reply `body`, `ai_body` and `error` (50 MB accepted); lone surrogates raise `UnicodeEncodeError`.
- **F7 retention blocks behind row locks** (no `lock_timeout`; consider `SKIP LOCKED` / `lock_timeout`), and `get_lead` leaves a transaction open.
- **F8 read methods do not roll back;** wrong-type ids raise `DataError` / `AttributeError`, not `ValueError`.
- **F9 pre-commit gate:** `git mv code.py code.md` is treated as docs-only (add `--no-renames`); the `.env` block does not cover `.envrc`, `prod.env`, `.ENV`.
- **F10 misc:** channel not validated; empty name/email/message accepted; `anonymize_older_than` accepts a future cutoff (wipes everything); no per-lead erasure method (DSGVO Art. 17); `audit_log.details` is not blanked by retention; `alembic downgrade base` drops all data; docker-compose publishes 5433 on all interfaces with password `leads` (dev only, bind to 127.0.0.1 before any shared host); dedup is bypassable by spelling variants until 4b rate limits.
