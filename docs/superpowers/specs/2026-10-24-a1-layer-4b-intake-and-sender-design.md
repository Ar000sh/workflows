# A1 Layer 4b (intake API and sender): Design Spec

> Part of the A1 lead responder (main spec: `2026-10-05-a1-lead-responder-design.md`, section 14 lists the layer 4 failure modes). 4b follows 4a (data foundation, merged 2026-10-24). 4c (auth and audit), 4d (dashboard) and 4e (demo site, end-to-end) come after. Read the A1 `REVIEW.md` section "Carry into the 4b/4c/4d plans" together with this file.

## 1. Purpose and business outcome
Outcome 1, **never miss a lead**: a website form posts a lead, the patient gets an acknowledgment or auto-reply by email within 60 seconds, and sensitive leads wait for staff approval. 4b is the first layer where the layer 1-3 pipeline (`process_inquiry`) meets the database and a real email send.

ROI is unchanged from the A1 README (leads not lost, staff minutes saved per routine reply). 4b adds no new ROI claim.

Learning goals: a service layer with a clear split of rules; claim-before-send (compare-and-set) so an email is never sent twice; a typed sender result; background work in FastAPI; a scheduled sweep job.

## 2. Context and constraints
- Market: Germany, DSGVO. Fake data only. Python 3.x pinned in `.python-version`; Postgres 16 and Mailpit in Docker (4a).
- Only redacted message text goes to Claude (unchanged). The sender sends only to the form's own email address.
- No new AI calls in 4b, so no new model cost per lead. Pipeline cost is bounded by the global hourly cap on AI-processed leads (section 4); a run that outlives its deadline can still finish and spend its few cents.
- Out of scope here: staff login and audit rows (4c), approve/edit/reject/resend pages (4d), the demo site (4e), n8n senders (layer 5).

## 3. Autonomy decision
**Workflow, not an agent** (same as A1 section 13). Every step of intake and sending is fixed in code; the model is called only inside the existing pipeline. How we check it: the end-to-end check in section 11 and the attack pass. No change to this decision is expected from 4b.

## 4. Decisions
Tariq's = decided by Tariq (in the A1 review or the 4b brainstorm 2026-10-24). Mine = Claude's call, to be checked while reading.

| Decision | Choice | Why | Whose |
|---|---|---|---|
| Layering | API validates input; a **service** holds business rules; the **repository** is database access and keeps its own commit (one method = one transaction); the database enforces hard limits | Separation of concerns; commit stays in the repository, so 4c writes the audit row inside the repository methods | Tariq's |
| Validation moves out of the repository | Length limits, NUL stripping and enum checks move to the API request model; limits are defined once in a shared constants module (`limits.py`) used by the model and `db/models.py`; `_require_aware` stays in the repository | Each rule lives in one layer. See the table in section 5 | Tariq's |
| Claim before send | New reply status `sending`. `approved -> sending` (or `drafted -> sending` for acknowledgments and auto-replies, `failed -> sending` for a resend) is one conditional UPDATE; only the winner sends. Then `sent` or `failed` | The conditional UPDATE from 4a protects the recording, not the send. Two workers or a double click must not email twice | Tariq's (recommended by Claude) |
| Stuck `sending` replies | **By kind.** Acknowledgments: retried automatically by the sweep after a timeout, up to a cap. Drafts (and auto-replies): never retried automatically; they wait for staff ("check whether this was delivered") | A duplicate acknowledgment is harmless; a duplicate staff-approved medical reply is not | Tariq's |
| How sending runs | Plain Python functions (`ReplyService`) plus a scheduled sweep script, like `run_retention.py`. No long-running worker, no n8n in the sender | Testable code; the scheduler is chosen with the deployment layer | Tariq's |
| Lead status after routes 1 and 2 | Stays `acknowledged`, flagged `needs_manual_review`, until staff mark it `done` (handled by phone). The transition table already allows `acknowledged -> done` | Matches the code since 4a and the spec; confirmed here | Mine (confirmed unless Tariq objects) |
| `sending` means "outcome unknown"; `failed` means "known not sent" | A send that times out, or a process that dies mid-send, leaves the reply in `sending`. A refused or unreachable provider after the retries ends in `failed` | One state per meaning, so the sweep and the dashboard can act without guessing. This **amends A1 section 7** (a timeout used to become `failed` with "timeout: may have been sent") | Mine |
| New columns on `replies` | `claimed_at` (timestamptz, set at the claim) and `send_attempts` (int, default 0, counts claims) | `claimed_at` tells the sweep what is stuck; `send_attempts` caps the automatic retries. Neither holds personal data | Mine |
| Rate limiting | **Per-email limit** in the database: more than `MAX_LEADS_PER_EMAIL_HOUR` (default 3) or `MAX_LEADS_PER_EMAIL_DAY` (default 10) leads for one mailbox (normalized key, see "One counter per mailbox"), counted from saved leads **including the lead itself** (so two simultaneous posts both see each other, probe P7): the lead is saved and flagged `needs_manual_review`, **no email of any kind is sent**, no AI. **Global cap**: above `MAX_AI_LEADS_PER_HOUR` (default 60) leads in the last hour: lead saved, fixed acknowledgment only, no AI, flagged. Per-IP limits, honeypot and captcha stay deferred (A1 section 12) | An acknowledgment per message would let anyone mail-bomb a victim's address with different texts (found by the builder check); the per-email cap bounds it. A real patient who writes a fourth time in an hour waits for staff, who see the flagged lead. The global cap bounds API spend. No IP is stored (DSGVO). Numbers are config defaults, my guess, tune later | Mine |
| Lead status vs send outcome | The lead moves to `auto_replied` only after its auto-reply is `sent`. If the auto-reply ends `failed`, or is left in `sending`, the lead is flagged `needs_manual_review` and moves `classified -> acknowledged` so it lands in the staff queue | A lead must never look handled when the patient got nothing. Uses existing transitions | Mine |
| Discarding a late pipeline result | New repository writes are conditional on the lead's status, checked under the row lock the repository already takes (`set_classification` only while the lead is `new`; the status moves already are conditional). A late result that finds the lead moved on writes nothing and sends nothing | A check in the service followed by a write would be racy; the lock closes it. Needs a two-session race test | Mine |
| Cost of an orphaned pipeline run | Python cannot cancel the blocking run, so after the deadline it may finish in the background and spend a few cents; its result is ignored. Making LLM calls cancellable (a change to `shared/llm.py`) is parked | Bounded by one run per lead and the global hourly cap; avoids touching `shared/` in 4b | Mine |
| Settings and practice | One practice, hard-wired for 4b: `llm`, `practice`, `faq` are built once at app start from `config.py` and `data/`. New config: SMTP host, port, TLS, timeout; From and Reply-To address; per-language subject text (fixed); CORS origins; the limits above; `SENDING_TIMEOUT_MINUTES`, `MAX_SEND_ATTEMPTS`, `NO_REPLY_SWEEP_MINUTES` (the full list with defaults is in `.env.example` and the A1 README). The route-1 fallback acknowledgment uses the DE+EN combined template with the emergency info and `practice.is_open(now)`, same as the unsupported-language path | Multi-practice is out of scope (A1 section 12) | Mine |
| Staff "resolve stuck draft" | 4b provides `ReplyService.send` and the transitions (`sending -> sent`, `sending -> failed`). The staff-facing resolve and resend actions, with auth and audit, are built in 4d on `set_reply_status` | 4b has no staff endpoints | Mine |
| First acknowledgment has one winner (probe P1) | New repository method `acknowledge_lead(lead_id, reply)`: the conditional lead-status move to `acknowledged` and the insert of the acknowledgment reply happen in **one transaction**; the loser gets `InvalidTransition` and creates nothing. Every writer of a first acknowledgment (pipeline routes, deadline fallback, sweep) goes through it | The deadline fallback, a late pipeline result and two overlapping sweeps could otherwise each add one (two emails), and a crash between the move and the insert would leave an `acknowledged` lead with no reply | Tariq's (accepted 2026-10-24) |
| Deliberately no email (probe P2) | New boolean column `leads.send_suppressed` (migration). Set when the per-email limit is exceeded: the lead moves `new -> acknowledged` flagged `needs_manual_review` with no reply. Sweep part 2 covers `new`, `classified` and `acknowledged` leads with no reply, and skips suppressed ones | The sweep must tell "no email on purpose" from "acknowledgment lost" | Tariq's (accepted) |
| One counter per mailbox (probe P3) | The per-email limit counts on a normalized key: lower-case, `+tag` removed; dots removed only for `gmail.com` and `googlemail.com`. The stored email stays as typed; the key is computed in one function used by intake. The key is derived from the email, so retention blanks it with the lead (builder decides if it is a column or computed in the query, and must check it against the anonymized-lead rules) | `victim+1@x.com` and `victim+2@x.com` reach one inbox; without this the cap is trivial to bypass | Tariq's (accepted) |
| Request body size (probe P5) | A hard limit (default 32 KB, `MAX_BODY_BYTES`) checked on `Content-Length` and while reading, before JSON parsing; over the limit gives `413` and nothing is stored | Field limits only apply after the whole body was read | Tariq's (accepted) |
| Concurrency of pipelines (probe P6) | At most `MAX_CONCURRENT_PIPELINES` (default 10) run at once (a semaphore around the background task). Above it the lead gets the fixed acknowledgment only (through `acknowledge_lead`), flagged, no AI. **Rule: no database transaction or row lock stays open across an AI or SMTP call**: the claim commits before the send | Orphaned runs after a deadline must not exhaust the thread or connection pools, and a lock held across a slow call would block retention and other writers | Tariq's (accepted) |
| No personal data in logs (probe P9) | Log lines carry ids and event names only: never email, name, phone, message or reply text, also not inside exception text that is logged. Existing pipeline events are checked for this | Logs would keep personal data after the 90-day retention | Tariq's (accepted) |
| Request format | JSON only (`POST /leads`); the 4e demo site will use `fetch`, n8n posts JSON later | Simplest; one validation model | Mine |
| Deadline on the pipeline | The background task has an overall deadline (default 45 s). On expiry the route-1 acknowledgment is sent and the lead is flagged; a late pipeline result is discarded | The pipeline's retries can take about 13 minutes; the patient must not wait for them | Mine |

## 5. Architecture and components
```
 form ──POST /leads──► API (schemas, limits)
                          │ validated LeadIn
                          ▼
                    IntakeService ──► Repository.create_lead_if_new ──► Postgres
                          │ (duplicate: stop, same success answer)
                          │ background task, deadline 45 s
                          ▼
                    process_inquiry (layers 1-3, unchanged)
                          │ PipelineResult
                          ▼
                    IntakeService: set_classification, add_reply (each reply), lead status
                          │ acknowledgment / auto-reply replies
                          ▼
                    ReplyService.send(reply_id) ──claim──► Repository.set_reply_status(SENDING)
                          │ only the winner continues
                          ▼
                    Sender (Protocol) ◄── SmtpSender (Mailpit in dev)
                          │ typed SendResult
                          ▼
                    set_reply_status(SENT | FAILED | stays SENDING on timeout)

 scheduler ──► scripts/run_sweep.py ──► ReplyService.sweep()  (stuck acknowledgments, unsent first replies, leads with no reply)
```

| Unit | Responsibility | Depends on |
|---|---|---|
| `limits.py` | One definition of every length limit (name, email, phone, message, ...) | - |
| `api/schemas.py` | `LeadIn`: required consent, email format, size caps, NUL stripping, name cleaning. Produces cleaned text; `dedup_key` is computed on cleaned text | `limits` |
| `api/app.py` | `POST /leads` (answers fast, hides duplicates), `GET /health`; body-size limit before parsing (`413`); session per request via `make_session_factory`; CORS allowlist from config; error answers never show internals | services |
| `services/intake.py` | Duplicate decision, rate limits (normalized mailbox key), concurrency cap on pipelines, running the pipeline under the deadline, writing results, choosing lead status, the fallback acknowledgment (route 1) | repository, pipeline, `ReplyService` |
| `services/replies.py` | `send(reply_id)`: refuse an anonymized lead, claim, call the sender, record the result; `sweep()`: retry stuck acknowledgments up to the cap, send first replies left `drafted`, give leads with no reply the fallback acknowledgment | repository, sender |
| `sender.py` | `Sender` Protocol, `SendResult` (`ACCEPTED`, `FAILED`, `TIMEOUT`), `SmtpSender` with retry policy (A1 section 7: up to 3 retries with backoff on a refused or unreachable provider, none after a timeout) | smtplib, config |
| `repository.py` | Loses its input rules (table below); gains `acknowledge_lead` (status move + acknowledgment reply in one transaction), conditional `set_classification`, `claim` through the extended transition tables, plus queries for the sweep (stuck `sending`, first replies left `drafted`, leads with no reply, recent-lead counts) | Postgres |
| `scripts/run_sweep.py` | The unit a scheduler runs; same shape as `run_retention.py` | `ReplyService` |

**Where each rule lives after 4b**

| Rule | Home | Note |
|---|---|---|
| Length limits, NUL stripping, name cleaning, consent, email format | `api/schemas.py` | DB CHECKs remain the last line. Bypassing the API now gives a database error instead of silent cleanup |
| Enum values | Type hints plus DB status CHECKs | Only the friendly error message is lost |
| Naive datetimes refused | Repository (`_require_aware`) | A wrong timezone silently changes meaning |
| "Is this a duplicate?" | `IntakeService` | The advisory lock and the 2-minute lookup stay repository queries; a unique constraint cannot express a time window |
| Allowed status moves | `LEAD_TRANSITIONS` / `REPLY_TRANSITIONS` in `db/models.py`, applied by the repository's conditional UPDATE | One table, one place. This is a hard rule enforced under concurrency, which is why it stays at the data layer |
| Refusing an anonymized lead | Service checks first (clear error); repository keeps the check under its row lock | **Two layers on purpose:** the service check can be stale by the time of the write, only the lock closes the race with a running retention batch |
| Refusing to send to an anonymized lead | `ReplyService.send` | No email address exists any more |

Doc and test housekeeping that belongs to the move: delete the `MAX_*` copies in the repository; move the "too long" and NUL tests to the API layer; keep the attack-probe regression tests in `tests/db/` green throughout (they are the check that nothing was lost).

### Reply statuses and transitions after 4b
Reply statuses: `drafted | approved | sending | sent | rejected | failed`. Lead statuses are unchanged: `new | classified | auto_replied | acknowledged | awaiting_approval | replied | done`.
```
drafted ─┬─ approve (staff, 4d) ─► approved ─┐
         ├─ reject (staff, 4d) ──► rejected (final)
         ├─ claim (acknowledgment, auto-reply) ──┤
         └─ refused (no address) ─► failed       ▼
                                             sending ──┬─ accepted ─────────► sent (final)
                                                ▲      ├─ known not sent ───► failed
                                                │      └─ timeout / crash: stays "sending" (outcome unknown)
                              failed ── claim ──┘  (staff resend in 4d; sweep retry of a stuck acknowledgment)
```
`REPLY_TRANSITIONS` as built: `drafted -> {approved, rejected, sending, failed}`, `approved -> {sending, failed}`, `sending -> {sent, failed}`, `failed -> {sending}`; `sent` and `rejected` stay final. `failed -> sent` and `failed -> failed` were removed: the only way back from `failed` is a new claim. `approved -> failed` and `drafted -> failed` are the refusal when the lead has no address any more. Which kind may be claimed from which status is a service rule on top of the table (`ReplyService.SEND_FROM`): acknowledgments and auto-replies from `drafted` or `failed`, staff drafts only from `approved` or `failed`. A staff member resolving a stuck draft in 4d moves it `sending -> sent` (confirmed delivered) or `sending -> failed` (not delivered, then resend). Needs an Alembic migration (status CHECK, the two columns) and the changed tables with their test.

## 6. Data and flows
**Intake (`POST /leads`)**
1. Validate and clean (`LeadIn`). Reject: missing consent, invalid email, over-size fields. Nothing stored on rejection.
2. `IntakeService.receive(lead)` (cheap, while the form waits): `create_lead_if_new`; a duplicate returns the same success answer to the form and does nothing else. Then the per-email check on the saved lead: over the limit means `send_suppressed`, flag it, move it `new -> acknowledged` and stop (no email, no AI). It returns `Received(lead_id, run_pipeline)`, or `None` for a duplicate.
3. Answer the form (`202 {"status": "received"}`, the same for every case), then run `IntakeService.process(lead_id)` in a background task when `run_pipeline` is true. `process` first checks the global AI cap and takes a free pipeline slot; over the cap or with no free slot the lead gets the fixed acknowledgment only (through `acknowledge_lead`), flagged. Otherwise the pipeline runs under the deadline. (Build ruling 3: these two checks moved from `receive` to the start of `process`, so the form never waits for a send.)
4. Pipeline result written: `set_classification`, one `add_reply` per generated reply, lead status (`classified` then `auto_replied`; or `acknowledged` for sensitive; routes 1 and 2 `acknowledged` flagged; sensitive with a draft later `awaiting_approval`).
5. Auto-replies and acknowledgments go to `ReplyService.send`. Drafts are not sent; they wait for staff (4d).
6. Any exception anywhere in steps 4-5 sends the route-1 acknowledgment and flags the lead (A1 section 14 row 7).

**Sending (`ReplyService.send`)**: refuse if the lead is anonymized or has no email; claim `-> sending` (set `claimed_at`, `send_attempts + 1`); on `InvalidTransition` someone else won, do nothing and report "already handled"; call the sender; record `sent` (with `sent_at`) or `failed` (with `error`); on `TIMEOUT` leave it in `sending`.

**Sweep (`ReplyService.sweep`, scheduled every few minutes)**: (1) acknowledgments in `sending` with `claimed_at` older than `SENDING_TIMEOUT_MINUTES` (default 10) and `send_attempts` below `MAX_SEND_ATTEMPTS` (default 3): move `sending -> failed`, then claim and send again; at the cap they stay for staff. Drafts and auto-replies in `sending` are never touched. (1b) Acknowledgments and auto-replies still `drafted` (written by `acknowledge_lead`, never claimed: the writer died or the database failed before the send) and created more than `NO_REPLY_SWEEP_MINUTES` ago (strictly), lead not anonymized: `send` them; the claim stops two sweeps from both sending. Staff drafts (kind `draft`) are never sent by the sweep. (2) Leads in `new`, `classified` or `acknowledged` older than `NO_REPLY_SWEEP_MINUTES` (default 5) with no first reply, not anonymized and not `send_suppressed`: send the route-1 acknowledgment through `acknowledge_lead` (a lead already past `new` or `classified` keeps its status; the method's conditional move is what stops two sweeps from both acting) and flag them.

**Retention** is unchanged. No new personal data is stored (no IPs, no new text columns).

## 7. Error handling
| Failure | Behavior |
|---|---|
| Database down when a lead arrives | The form gets an error; nothing is stored or sent |
| Any AI call fails | Existing pipeline safety default (sensitive + manual review) |
| Pipeline exceeds the deadline or raises any exception | Route-1 acknowledgment, lead flagged `needs_manual_review`; late result discarded |
| Provider unreachable or refuses | Up to 3 retries with backoff, then `failed` with `error`; staff resend in 4d |
| Send times out | Reply stays `sending` (outcome unknown). Acknowledgment: sweep retries after `SENDING_TIMEOUT_MINUTES`, up to `MAX_SEND_ATTEMPTS` claims in all. Draft or auto-reply: waits for staff |
| Process dies mid-send | Same as a timeout: the reply is in `sending` |
| Process dies after saving the lead | Sweep part 2 sends the route-1 acknowledgment |
| Process dies (or the database fails) after the first reply is written but before its claim | Intake still tries the send in that run; otherwise sweep part 1b sends the `drafted` reply after `NO_REPLY_SWEEP_MINUTES` |
| Two workers or two clicks try to send | One claim wins; the other gets `InvalidTransition` and does not send |
| Lead anonymized before the send | No send; reply moves to `failed` (no error text kept). A reply already `failed` stays as it is |
| Stuck acknowledgment given up by the sweep | `sending -> failed` with the fixed error `stuck: retried by sweep` (no personal data, so it is also written when the lead is already anonymized; the following claim is then refused for that lead), then claimed and sent again |
| A sweep run | Returns `SweepResult(retried_acknowledgments, fallback_acknowledgments, left_for_staff, sent_stale_drafted)`; `run_sweep.py` prints the four counts |
| Over the per-email limit | Lead saved, `send_suppressed`, flagged, `acknowledged`, no email, no AI; staff see it in the queue; the sweep skips it |
| Request body over the limit | `413`, nothing stored |
| No free pipeline slot | Lead saved, fixed acknowledgment only, no AI, flagged |
| Over the global AI cap | Lead saved, fixed acknowledgment only, no AI, flagged |
| Auto-reply ends `failed` or stays `sending` | Lead flagged `needs_manual_review` and moved to `acknowledged` (staff queue); not marked `auto_replied` |
| `failed` acknowledgment | Not retried by the sweep; staff resend in 4d (the lead is flagged) |

## 8. Failure modes (pre-mortem)
Rows 5-11 of A1 section 14 are this layer's. Added for the claim design and the abuse limits. Status: **design** / **test** / **parked**.

| Risk | What happens | Stopped by | Status |
|---|---|---|---|
| 5 Database down or slow at intake | Error to the form, no half-saved state, nothing sent | API handler, test with a failing session | test |
| 6 Process dies after the lead is saved | Lead without any reply | Sweep part 2 | test |
| 7 Any exception in the pipeline | Patient gets no answer | Catch-all sends route-1 acknowledgment | test |
| 8 Slow AI delays the "instant" acknowledgment | Patient waits minutes | Deadline 45 s, fallback acknowledgment | test |
| 9 Form abuse: huge body, no consent, bad email, hostile name, flood | Cost or junk | Schema limits, consent/email validation, name cleaning, duplicate window, per-email and global limits | test |
| 10 Form becomes a mail relay | Stranger receives attacker-influenced text | Reply only to the form's address; final text is guardrailed; per-email limit; fixed subject; header injection blocked by email validation (no newline) | test |
| 11 Email sent twice or never recorded | Duplicate or silent loss | Claim before send; typed result; `sent` final | test |
| 11a Two senders race on one reply | Double email | Conditional UPDATE claim; two-session race test shown failing without the claim | test |
| 11b Stuck `sending` acknowledgment retried forever | Mail loop to one patient | `MAX_SEND_ATTEMPTS` cap | test |
| 11c Stuck `sending` draft retried automatically | Duplicate medical reply | Sweep never touches drafts or auto-replies; test | test |
| 11d Sweep and a late original send race | Duplicate acknowledgment | Accepted (harmless for acknowledgments); the claim still serializes the two sends | design |
| 11e Pipeline result arrives after the fallback acknowledgment | Two acknowledgments or a wrong status | Repository writes conditional on lead status under the row lock; two-session race test, shown to fail without the condition | test |
| 11j Mail-bomb a victim with many different messages | Victim flooded | Per-email cap sends nothing once exceeded | test |
| 11k Auto-reply fails and the lead looks handled | Patient gets nothing, nobody knows | Lead becomes `auto_replied` only after `sent`; otherwise flagged into the staff queue | test |
| 11f Lead anonymized between claim and send | Email to a blanked address | `send` re-reads the lead; no address means no send | test |
| 11g SMTP header or body injection through name or message | Forged headers | Email validation, name cleaning, fixed subject, body only as the body | test |
| 11h Many different addresses, many leads | API credit burned | Global hourly cap on AI-processed leads | test |
| 11i Migration breaks existing rows or the status CHECK | App fails to start | Migration test (models vs migration compare, upgrade/downgrade) | test |
| Per-IP limits, captcha, honeypot | Bots | Deferred by A1 section 12 | parked |

**Plan-mode probe, 2026-10-24 (edge-case-probe, concurrency as focus).** Ranked by impact x likelihood. **Decided by Tariq 2026-10-24:** P1, P2, P3, P5, P6 and P9 are folded into the design above (section 4); P4 is parked; P7, P8, P10, P11 and P12 are tests or housekeeping the plan carries.

| # | Risk | What happens | Proposed fix | Status |
|---|---|---|---|---|
| P1 | The fallback acknowledgment has two writers (deadline fallback vs the pipeline finishing late; two overlapping sweeps; sweep vs original task). `add_reply` is not conditional, so two acknowledgments (or, between the status move and the reply insert, a crash leaves an `acknowledged` lead with no reply) | Duplicate or lost acknowledgment, a patient with no answer and a lead that looks handled | One new repository method, `acknowledge_lead(lead_id, reply)`, does the conditional lead-status move **and** the reply insert in **one transaction**; the loser gets `InvalidTransition` and creates nothing. The lead-status move is the claim for the first acknowledgment. Race test with two sessions, shown to fail without it | in design, test |
| P2 | Sweep part 2 sends an acknowledgment to the leads the per-email cap deliberately left without email, undoing the cap | Mail-bomb protection defeated after 5 minutes | Over-limit leads move `new -> acknowledged` flagged and carry a marker for "no email on purpose" (a boolean column `send_suppressed` in the same migration); sweep part 2 also covers `acknowledged` leads with no reply, but skips suppressed ones | in design, test |
| P3 | Gmail-style aliases (`victim+1@x.com`, `v.ictim@gmail.com`) are different strings but one mailbox, so the per-email cap is bypassed | Mail-bomb with unlimited distinct addresses | Count the limit on a normalized key (lower-case, strip `+tag`; dots only for gmail/googlemail) | in design, test |
| P4 | Many different victim addresses, one fixed acknowledgment each: the cap is per address, the global cap only drops the AI | The form is a (low-grade) mail relay for the acknowledgment text and the name | Add `MAX_EMAILS_PER_HOUR` (default 120): above it, lead saved and flagged, no email. A practice never gets 120 real leads an hour | parked (Tariq, 2026-10-24; see section 14 and REVIEW.md) |
| P5 | Request body size: pydantic only checks field lengths after the whole body has been read and parsed | A 500 MB POST uses server memory before any limit applies | Body-size limit (about 32 KB) in a middleware or the server, before parsing; test with an oversized body | in design, test |
| P6 | Unbounded concurrent pipelines: every lead starts a worker thread that may outlive its 45 s deadline | Thread pool and DB pool exhausted; the form stops answering | Semaphore `MAX_CONCURRENT_PIPELINES` (default 10); above it, lead gets the fixed acknowledgment only and is flagged. Never hold a DB transaction or row lock open across an AI or SMTP call (the claim commits before sending); test that the sender is called with no open transaction | in design, test |
| P7 | The rate-limit count races: two simultaneous posts both read "2 so far" | Limit exceeded by a few | Count after saving, including the lead itself: two racers each see both and both are limited (safe side) | design, test |
| P8 | The DB is down right after the email is accepted: `sent` cannot be recorded | Reply stays `sending`; an acknowledgment gets retried (harmless duplicate), a draft waits for staff | Already handled by `sending` meaning "unknown"; add a test that fakes a failing record step | design, test |
| P9 | Logs hold personal data (email, name, message) in error lines | Personal data survives retention in log files | Log ids and event names only; one test that fails if the email or message text of a test lead appears in captured logs | in design, test |
| P10 | Whitespace-only message or name, lone surrogates, 254+ char email, IDN: pass or crash in odd places | 500 error, or `process_inquiry` raises on empty text | `LeadIn` rejects whitespace-only after cleaning; JSON surrogates rejected; email length cap; the 500 body never shows internals | test |
| P11 | Migration downgrade or upgrade with existing `sending` rows and the changed status CHECK | Migration fails on real data | Migration test with rows in every status; downgrade maps `sending` to `failed` or refuses with a clear message | test |
| P12 | Docs still say a timeout becomes `failed` or "no automatic retry" (A1 spec sections 5 and 7, section 14 row 11, REVIEW.md claim options, README) | Docs contradict the code | Grep list for the plan's last task: `timeout: may have been sent`, `no automatic retry`, `failed` with Resend | design |

**Rest of the checklist, nothing to add:** delimiters and prompt injection (no new AI input; header injection is row 11g), exact boundaries (limit and limit+1, the 120 s window and `SENDING_TIMEOUT` have tests in section 11), unreviewed output (only the fixed acknowledgment and guardrailed auto-replies, unchanged), word lists (none added).
| Scheduler choice and alerting on a failed sweep | Sweep silently stops | Chosen with the deployment layer | parked |

## 9. Safety type
Type A (customer-facing, `devsetup/safety-patterns.md`). Applies: an error leads only to human review or a fixed acknowledgment, never to a wrong automatic action; deterministic guardrails on final text (existing); nothing unreviewed goes out except the fixed acknowledgment and guardrailed auto-replies; 0 sensitive-to-routine target is unchanged. Does not apply: new prompt-injection defenses (no new AI input).

## 10. Models and context
No change: Haiku classifies, Sonnet drafts (`devsetup/model-choice.md`). 4b adds no prompts. After 4b no `/run-evals` is needed unless the pipeline's prompts, router or guardrails change.

## 11. Testing and evals
- **Unit tests:** `LeadIn` (every limit, consent, NUL, hostile name, newline in email); transition tables (new moves, finals); `SmtpSender` retry policy with a fake SMTP (error retried 3 times; timeout not retried; accepted); `ReplyService.send` and `sweep` with a fake sender (each row of section 7); intake with a fake pipeline (duplicate, rate limit, deadline, exception path).
- **Database tests (skip without Postgres):** the claim race with two sessions, shown to fail with the claim removed; sweep selects exactly the right replies; migration upgrade/downgrade and the models-vs-migration compare; the attack-probe regressions moved from the repository.
- **API tests:** FastAPI `TestClient` against the test database; failing database returns an error and stores nothing.
- **End-to-end (needs Docker):** post a routine lead, an acknowledgment/auto-reply arrives in Mailpit; a sensitive lead gets an acknowledgment and a draft row; a Turkish-language lead gets the DE+EN acknowledgment with no draft; a duplicate post sends nothing; hostile posts (500 KB body, whitespace message, `+tag` and Gmail-dot aliases) get 4xx or are suppressed. Built as `tests/db/test_end_to_end.py` (real `SmtpSender` to Mailpit, fake LLM; skips when Mailpit is down).
- **Log hygiene:** `tests/db/test_logging.py` posts canary leads through the whole flow (normal, failing LLM, failing sender, guardrail rejection, over the limit, duplicate, sweep) and fails if any canary appears in a log record.
- **Attack pass:** after the last task, `/edge-case-probe` in attack mode by a fresh agent, with concurrency named as the focus (it is a change to locks and transactions). The main session runs it, not the implementers.
- Unit and DB tests gate commits through the pre-commit hook; never `--no-verify`.

## 12. Build layers
| Step | Concepts learned | Done when |
|---|---|---|
| 4b-1 `limits.py`, `LeadIn`, validation moved out of the repository | One rule one layer, request validation | Old repository tests moved, attack regressions green |
| 4b-2 Migration and transition tables for `sending`, `claimed_at`, `send_attempts`, `send_suppressed`; `acknowledge_lead` and conditional `set_classification` | Compare-and-set, migrations | Race test fails without the claim; migration test green |
| 4b-3 `Sender`, `SmtpSender`, `ReplyService.send` | Typed results, outbox pattern | Table in section 7 covered by tests |
| 4b-4 `IntakeService`, `POST /leads`, body-size limit, deadline, rate limits on the mailbox key, pipeline concurrency cap, no personal data in logs | Service layer, background tasks | API and e2e tests green |
| 4b-5 `ReplyService.sweep`, `run_sweep.py`, docs | Scheduled jobs, idempotence | Sweep tests green; README, REVIEW.md, spec section 7 amended |

## 13. Definition of done
Runs end-to-end from the A1 README on Mailpit; tests pass; README and A1 spec section 7 and the status flow match the code (grep for the old "timeout becomes failed" wording); A1 `REVIEW.md` has a 4b overview (what was built, table of public methods) and checklist; PROJECT.md status and decisions log updated; `devsetup/CATALOG.md` updated if anything is added; attack pass done and triaged.

## 14. Out of scope
Staff login and audit rows (4c) · dashboard pages and the approve/resend endpoints (4d) · demo site (4e) · n8n senders and webhooks (layer 5) · per-IP rate limiting, honeypot, captcha (A1 section 12) · a cap on the total number of emails the form sends per hour (probe P4, parked 2026-10-24) · an `EXCLUDE USING gist` duplicate constraint (parked in REVIEW.md) · a CHECK on `leads` for anonymized rows (the repository's locked guard already covers it and a CHECK cannot cover `replies`) · choosing the scheduler and alerting for the sweep and retention jobs (deployment layer) · delivery and bounce tracking.
