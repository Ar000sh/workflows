# Layer 4b: intake API and sender

> Part of the A1 review set; index in [../REVIEW.md](../REVIEW.md). Built 2026-10-24 on branch `a1-4b-intake-sender`. Sections "Carry into the 4c/4d plans" and "Go-live gate" are read before writing the 4c and 4d specs.

Spec: `docs/superpowers/specs/2026-10-24-a1-layer-4b-intake-and-sender-design.md` (section 4 holds every decision, section 8 the failure modes). Plan: `docs/superpowers/plans/2026-10-24-a1-layer-4b-intake-and-sender.md`. Branch `a1-4b-intake-sender`, commits `b1fc14b` to the docs commit at the end (`git log --oneline dc9846f..a1-4b-intake-sender`). The attack pass (fresh agent, concurrency as focus) is still to run in the main session.

## What 4b is and how its pieces fit
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

## 4b: your review, in this order (about 2 hours; paths under `track-a-dental/a1-lead-responder/`)
- [ ] **1. Run it (15 min).** README "Intake and sending (layer 4b)": `docker compose up -d postgres mailpit`, `alembic upgrade head`, `uv run pytest tests/db/test_end_to_end.py` (real SMTP into Mailpit, fake AI), then open http://localhost:8025 and look at the mails the tests sent. Optional, costs about $0.01: start `uvicorn lead_responder.api.main:app` and post the README's `curl` lead. Run `uv run python -m lead_responder.scripts.run_sweep` once.
- [ ] **2. The status tables (15 min).** Start with the diagrams in [status-flows.md](status-flows.md). `db/models.py` `REPLY_TRANSITIONS` and the migration `alembic/versions/e93e6e744ccc_sending_claim.py`; compare with the reply status flow in A1 spec section 5. Ask: is `sending` = unknown and `failed` = known not sent clear enough for staff?
- [ ] **3. Sending (30 min).** `sender.py` (how an error's stage decides FAILED vs TIMEOUT), then `services/replies.py` `send` and `_send` (steps 1-6 in the module docstring). Test to read: `tests/db/test_reply_service.py::test_two_sends_of_one_reply_at_once_send_one_email`.
- [ ] **4. Intake (30 min).** `services/intake.py` (`receive`, `process`, `_run_pipeline`, `_write_result`), then `repository.py` `acknowledge_lead`. Tests: `tests/db/test_repository_acknowledge.py::test_two_sessions_acknowledging_one_lead_exactly_one_wins`, `tests/db/test_intake_service.py`.
- [ ] **5. The API (15 min).** `api/app.py` (`_Guard`, the 422 handler that hides the submitted values) and `api/schemas.py`. Tests: `tests/db/test_api.py`.
- [ ] **6. The sweep (15 min).** `ReplyService.sweep` and the five repository queries (`stuck_sending_replies`, `failed_stuck_replies`, `unflagged_leads_with_stuck_replies`, `stale_drafted_first_replies`, `leads_without_first_reply`); tests `tests/db/test_sweep.py` (the three overlapping-sweeps tests).
- [ ] **7. Logs (5 min).** `tests/db/test_logging.py`, and the one change it forced: `pipeline._Run.event` logs the event name only.
- [ ] **8. Decide the items in "4b: to decide or confirm"** below, skim the build rulings and the Parked list, then decide on the merge (after the attack pass).

## 4b: to decide or confirm
- [ ] **Draft guardrail warnings are not stored in 4b.** The pipeline computes them, but `IntakeService` stores only the draft text; 4d recomputes the warnings when staff open a draft (and 4c stores the ones shown at approval in `audit_log.details`). Alternative: a column on `replies`.
- [ ] **`failed -> sent` and `failed -> failed` were removed from `REPLY_TRANSITIONS`.** The only way back from `failed` is a new claim (`failed -> sending`). A staff "it did arrive after all" for a `failed` reply is therefore not possible; for a stuck `sending` reply it is (`sending -> sent`).
- [ ] **Lead status for routes 1 and 2 confirmed:** they stay `acknowledged`, flagged, until staff mark them `done` (4b spec section 4, "Mine"; object if you disagree).
- [ ] **Auth errors and permanent 5xx are retried 3 times** (A1 spec section 7 says so; ruling 6). A wrong SMTP password then costs 4 logins per email, and some providers lock an account after repeated failures. Keep, or fail at once on auth errors?
- [ ] **The limits' numbers are guesses** (3 per mailbox per hour, 10 per day, 60 AI leads per hour, 10 parallel pipelines, 45 s deadline, 10 min stuck, 3 attempts, 5 min no-reply). Tune them with the practice before going live.
- [ ] **The pre-commit gate is close to 90 s.** The full suite ran 35-60 s during 4b, once 91 s, 43 s at the end (530 tests). The rule says ask you: keep the 90 s limit, raise it, or run only the relevant tests in the gate? Never bypassed so far.
- [ ] **Scheduler for `run_sweep` and `run_retention`:** nothing runs them yet. Choose with the deployment layer (cron or a Docker job on the VPS, Task Scheduler for a local demo, an n8n schedule trigger in layers 5-6) and add an alert when a job fails. Until then a lead whose processing died gets its fallback acknowledgment only when someone runs the sweep.

## Build rulings (decisions Claude took on your behalf during the build; each can be undone)
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

## Go-live gate (must be true before a real practice uses the form)
- [ ] **The sweep (and retention) are scheduled.** Without the sweep the "never left without a reply" safety net does not run at all: a deploy or restart kills the background tasks of the leads in flight, and only the sweep answers them. Retention is a DSGVO duty. Alert on `run_sweep` exit code 1.
- [ ] **Set `SMTP_LOCAL_HOSTNAME`** to the sending host's real FQDN (or the practice's mail domain). The default `localhost` is for development, and some mail servers reject or score down a non-FQDN HELO name.
- [ ] **The parked global email cap, per-IP limits and a captcha are done** before the form is on a public site (Parked: 4b, first item; A1 spec section 12).
- [ ] **Accept or change the per-mailbox cap's blind spot:** a real patient's 4th post within the hour is saved but gets no email, and only a staff flag shows it; the 4d dashboard that would show the flag is not built yet.
- [ ] **Threads under an SMTP slowdown:** fallback and acknowledgment sends run in the web server's pool of about 40 worker threads; a flood of posts while SMTP is slow (up to 10 s timeout and 7 s of retry waits per mail) can queue ordinary posts behind them.
- [ ] **Non-ASCII (IDN) addresses:** an address like `anna@müller.de` passes the form but the send ends in doubt (`sending`) and is retried pointlessly up to the cap. Decide: refuse non-ASCII addresses in `LeadIn`, or send with SMTPUTF8.

## Carry into the 4c/4d plans (from the 4b build)
- **4d must provide staff actions** on top of `set_reply_status` (each with auth and an audit row from 4c): resolve a stuck `sending` reply (`sending -> sent` "confirmed delivered" or `sending -> failed` "not delivered"), resend a `failed` reply (through `ReplyService.send`, which claims), approve and reject drafts. Show `InvalidTransition` / `ALREADY_HANDLED` as "already handled".
- **4d must refuse** leads with `anonymized_at` set or an empty email for any send action (`ReplyService.send` already returns `REFUSED`).
- **4d lists:** leads flagged `needs_manual_review`, `send_suppressed` leads (over the per-email limit: the patient got nothing), replies `failed`, replies stuck in `sending` (drafts and auto-replies wait there for staff; acknowledgments at `MAX_SEND_ATTEMPTS`).
- **4d recomputes draft guardrail warnings** when staff open a draft (they are not stored in 4b); 4c stores the warnings shown at approval in `audit_log.details`.
- **Scheduler and alerting** for `run_sweep` (every few minutes) and `run_retention` (daily): deployment layer.

## Parked: 4b
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
