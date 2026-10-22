# Review & Next Steps (Tariq's checklist)

> A1 review file; each project keeps its own REVIEW.md next to its README. Central place for work that needs **Tariq's review or decision**. Tick items off, add notes. Update it whenever a step finishes or new review work appears.
> Last updated: 2026-10-22

## Where we are
- A1 layers 1–3 (core logic) built; 120 mocked unit tests pass; Python pinned to 3.12 (`.python-version`).
- First live runs on 2026-10-22 (Haiku 4.5 classifier, Sonnet 5.5 drafter, effort low):
  - Classifier: category 44/45, risk 45/45, language 45/45, sensitive→routine **0**, injection failures **0**, cost $0.057.
  - Drafter: 18 auto-replies, 0 guardrail rejections, 0 API errors, cost $0.445 (about $0.01 per inquiry).
- Nothing after layer 3 exists yet (no API server, database, dashboard, n8n).

## Concepts to understand (ask me about anything unclear)
- **Pipeline:** redact (code) → classify (Haiku) → decide (code) → draft (Sonnet) → guardrails (code) → send or staff queue.
- **Workflow vs agent:** in a workflow, *our code* decides the steps; in an agent, *the LLM* decides the next step in a loop. A1 is a workflow on purpose: the safety decision must be deterministic code.
- **Context engineering:** better context (facts, style guides, examples) can replace a bigger model for writing tasks, but not for judgment on ambiguous input. We test this instead of assuming it.

## Steps

### 1. Read the code (about 1 hr) — Tariq
- [ ] Read in this order: `redactor.py`, `classifier.py`, `router.py`, `drafter.py`, `guardrails.py`, `pipeline.py` (all in `track-a-dental/a1-lead-responder/lead_responder/`).
- [ ] Run `uv run python -m lead_responder.scripts.try_inquiry --name Anna --message "..."` with a routine, a pain, and a Turkish message; watch each branch.
- [ ] Write down anything unclear and ask.

### 2. Review the eval results (about 1 hr) — Tariq
- [ ] Open the newest `track-a-dental/a1-lead-responder/eval_runs/drafts-*.md` (latest: `drafts-20261022-2323.md`) and read 5–10 replies. Check facts against `faq.md`, tone, and the "no diagnosis / no prices" rules. Note findings below.
- [ ] Rule on label `i05` (expected `medical_question`, Haiku said `pain_emergency`; both sensitive, so routing is the same).
- [ ] Skim all labels in `data/eval_inquiries.jsonl`. The spec says every label is reviewed by you before the score counts.
- [ ] Review `data/faq.md` and `data/practice.yaml` for correctness.

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
- [ ] FastAPI, Postgres, staff login and audit log, HTMX dashboard, Mailpit, Docker Compose, demo site. See spec section 9.
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

### Parked: redactor hardening (decided 2026-10-22: not now)
Done already: emails, phones, cards, IBANs, Versichertennummer become `[NUMBER]`; dates in d.m.yyyy, d/m/yyyy, d-m-yyyy, yyyy-m-d and times are kept.
Known gaps, in order of value, if we ever come back to them:
1. Scrub the form's own name/email/phone fields from the message text (deterministic; fixes signatures like "Mit Grüßen, Anna Müller").
2. Birth dates after a keyword (`geboren`, `geb.`, `Geburtsdatum`, `born`, `DOB`).
3. Street plus house number (`...straße 12`).
Not fixable by redaction: names of other people ("mein Sohn Max"), and the health content itself (Art. 9), which is why a real deployment needs the AVV with Anthropic. Every new pattern needs a "must not redact" test too. This is orientation, not legal advice.

## Open decisions for Tariq
- [ ] Monthly API budget (suggested $20 while learning); record in PROJECT.md Decisions log.
- [ ] Keep `.agents/` and `skills-lock.json` (grilling skills installed for another tool)?
- [ ] Add a "what enters the dev setup?" step to the definition of done?
