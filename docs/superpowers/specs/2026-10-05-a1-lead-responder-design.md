# A1 — BrightSmile Lead Responder: Design Spec

**Date:** 2026-10-05 · **Track:** A (local service business) · **Status:** approved after grilling session (2026-10-05)
**Context:** [PROJECT.md](../../../PROJECT.md)

## 1. Purpose
First project of the AI-automation learning plan. Two equal jobs:
1. **Learning vehicle** — teaches the fundamentals in deliberate layers: Claude API, structured output, evals, FastAPI, Postgres, Docker, n8n, webhooks, auth.
2. **Sellable demo** — a German dental practice ("BrightSmile Zahnarztpraxis", fictional, **Hannover**) never misses a website inquiry: routine questions answered in seconds, sensitive ones acknowledged instantly and drafted for staff approval.

**Business outcome:** *Never miss a lead* (outcome #1 in PROJECT.md). ROI story: instant replies + receptionist time saved on repetitive inquiries; measured by reply latency and draft-approval rate.

## 2. Context & constraints
- **Market: Germany.** DSGVO applies; dental inquiries may contain health data (Art. 9 special category). Practices book via **Doctolib** — replies link to it rather than building booking.
- Fake but realistic data only. **All phone numbers fictional, except 112.**
- **Languages: German + English** fully supported. Other languages handled minimally (§4.4).
- Runs locally via `docker compose up`. Cloud deployment (Hetzner / Supabase Frankfurt) is **out of scope** for A1.
- **Working mode:** Claude writes each layer and explains it; Tariq reviews and runs it. No exercises.
- **Tooling:** Python 3.11+, `uv`, git, Docker Desktop.
- **Prerequisite before layer 1:** Anthropic Console account + API key (spend limit to be chosen by Tariq).

## 3. Decisions
| Decision | Choice |
|---|---|
| Project role | Learning vehicle first, built in layers |
| Channels | Designed multi-channel; **website contact form first**; WhatsApp (360dialog/Twilio) and inbound email later as adapters |
| Autonomy | **Hybrid by risk:** routine → auto-reply; sensitive → instant acknowledgment + AI draft for staff approval |
| Auto-reply timing | Always (config switch `AUTO_REPLY_MODE = always \| outside_hours`) |
| Staff interface | **Web dashboard with real staff logins first**; email notification second |
| Architecture | **Python brain, n8n plumbing** |
| Practice knowledge | `faq.md` placed in the prompt (no RAG — that is A2); opening hours + holidays in structured config |
| Models | **Haiku** for classifier, **Sonnet** for drafter; both config values (exact IDs/prices verified against current docs at build time) |
| Data sent to Claude | **Message text only**, with emails/phone numbers redacted |
| Dashboard tech | FastAPI + Jinja2 templates + HTMX (no SPA, no build step) |
| Database | Postgres in Docker |
| Local email | Mailpit (catches all outbound mail) |

## 4. Architecture
```
 Demo website (Kontaktformular)
        │  layers 1–4: POST directly to API · layer 5+: via n8n webhook
        ▼
 ┌──────────── FastAPI "lead-responder" ─────────────────┐
 │ POST /leads → save lead → Redactor → Classifier → Router│
 │   routine     → Drafter → guardrails → auto-send       │
 │   sensitive   → template ack (+ safe-info paragraph)   │
 │                 + Drafter → approval queue             │
 │   unsupported language → template ack (DE+EN), no draft│
 │ Dashboard (login): list · status · approve/edit/reject │
 │ Sender ─► SmtpSender (Mailpit)    [layers 1–4]         │
 │        └► N8nSender (n8n sends)   [layer 5+]           │
 └───────────────────────┬───────────────────────────────┘
                         ▼
                 Postgres (Docker)
```

### 4.1 Components (one responsibility each)
| Unit | Responsibility | Depends on |
|---|---|---|
| `shared/llm.py` | Thin Claude client wrapper: model selection, retries (up to 3, exponential backoff), JSON parsing, token/cost logging. Reused by later tracks. | Anthropic SDK |
| `redactor` | Replaces emails with `[EMAIL]` and phone, card, IBAN and insurance numbers with `[NUMBER]` before any AI call. Names in free text are not removed (documented limitation). | — |
| `classifier` | One Claude call (Haiku) → validated JSON (Pydantic): `category`, `risk`, `urgency`, `language`, `reason` | `shared/llm` |
| `drafter` | Claude call (Sonnet). Writes (a) routine auto-replies, (b) the optional **safe-info paragraph** for acknowledgments, (c) drafts for staff. Uses `{name}` placeholder; name filled in locally after generation. | `shared/llm`, `faq.md` |
| `templates` | Fixed acknowledgment texts (DE, EN, DE+EN combined), with emergency variants | `practice` |
| `practice` | Reads structured practice config: opening hours, Niedersachsen public holidays, phone numbers, Notdienst info, Doctolib link; answers `is_open(now)` | config file |
| `guardrails` | Deterministic checks on the FINAL text (name filled in): booking link present as a whole link, no other links, no phone numbers/emails/IDs except the practice's own, no leftover placeholders, no price figures, no diagnosis terms, word limit (emergency numbers live in the fixed acknowledgment template, not in AI text) | — |
| `router` | Plain Python decision: which replies to create and which status to set. No AI. | — |
| `repository` | All DB reads/writes | Postgres |
| `sender` | `send(reply)` returning a typed result: `accepted`, `failed` (retryable), or `timeout` (outcome unknown). Implementations `SmtpSender`, `N8nSender`. Retry policy lives here (§7). | SMTP / n8n |
| `auth` | Staff login: argon2 password hashing, signed session cookie, lockout. CLI for account management (§5.1). | repository |
| `audit` | Records every staff action with user + timestamp | repository |
| `dashboard` | Server-rendered pages behind login: lead list with filters, lead detail, approve/edit/reject, resend; highlights drafts waiting > 4h; **two work queues: sensitive drafts first (also emergencies/urgency on top), routine drafts below**, so staff review and send, never write from scratch | repository, sender, auth, audit |
| `retention` | Anonymizes leads older than `RETENTION_DAYS` (default 90) | repository |
| demo website | Static BrightSmile page with contact form | — |
| n8n flows | `website-form` intake → `POST /leads`; `send-reply` webhook → email | API |

### 4.2 Classification
**Categories:** `appointment`, `new_patient`, `insurance`, `opening_hours` (typically routine); `pain_emergency`, `medical_question`, `complaint`, `pricing`, `other` (always sensitive). The classifier proposes `risk`; the router **forces sensitive** for the always-sensitive categories regardless of the model's `risk`.

**Keyword safety net (added 2026-10-22):** plain code (`router.mentions_urgent_signs`) scans the redacted message for pain/injury words in German and English (Schmerz, weh, blutet, geschwollen, Notfall, 112, pain, aching, bleeding, swelling, ...). A hit forces the sensitive route with emergency info, even if the classifier said routine. It can only make the outcome safer; a false alarm costs staff one draft. The pipeline logs a `keyword safety net` event when it overrides the classifier.

**Safety default:** classifier failure, invalid JSON after 3 retries, or guardrail failure on an auto-reply → lead treated as **sensitive** with `needs_manual_review`. An error can only cause human review, never a wrong auto-reply.

### 4.3 Acknowledgments (sensitive leads)
- **Never skipped.** Every sensitive lead gets an instant acknowledgment.
- Body = **fixed template** (in the lead's language) + optional **AI safe-info paragraph** that adds only FAQ facts relevant to the inquiry (e.g. general insurance coverage for a pricing question). The paragraph passes `guardrails`; if it fails or the AI finds nothing safe to add, it is dropped and the template goes out alone.
- **Emergency variant** (`pain_emergency` or keyword net), chosen by `practice.is_open(now)`. Only the acknowledgment carries emergency numbers; the staff draft for pain addresses the concern and offers an urgent slot without repeating them:
  - open → "please call us now at <practice phone>"
  - closed (incl. Niedersachsen public holidays) → zahnärztlicher Notdienst info
  - always → "for life-threatening symptoms (heavy bleeding, facial swelling with difficulty breathing) call **112**"

### 4.4 Unsupported languages (not DE/EN)
Lead saved, flagged `language_unsupported`, treated as sensitive; **DE+EN combined template acknowledgment** sent, always with the emergency info (the classifier and keyword net cannot be trusted in other languages); **no AI draft**; the lead is flagged `needs_manual_review` so the dashboard shows it. No lead is ever dropped.

### 4.5 Auto-replies (routine leads)
Sent immediately (`AUTO_REPLY_MODE=always`, default) or only outside opening hours (`outside_hours`; during opening hours routine leads get the fixed instant acknowledgment (no AI paragraph) and an AI draft for staff to review and send). Auto-replies always include the Doctolib link.

## 5. Data model
**`leads`** — `id`, `created_at`, `channel` (`web_form`; later `whatsapp`, `email`), `name`, `email`, `phone` (nullable), `message`, `consent_at`, `category`, `risk`, `urgency`, `language`, `language_unsupported`, `ai_reason`, `needs_manual_review`, `status`, `anonymized_at`.

**`replies`** — `id`, `lead_id`, `kind` (`auto_reply` | `acknowledgment` | `draft`), `ai_body`, `body`, `status` (`drafted` | `approved` | `sent` | `rejected` | `failed`), `error`, `approved_by` (→ `staff_users.id`), `sent_at`, `created_at`.

**`staff_users`** — `id`, `name`, `email`, `password_hash` (argon2), `failed_attempts`, `locked_until`, `created_at`, `disabled_at`.

**`audit_log`** — `id`, `user_id`, `action` (`approve` | `edit` | `reject` | `resend` | `login` | `logout`), `lead_id`, `reply_id` (nullable), `details` (JSON, e.g. guardrail warnings shown at approval), `created_at`. Page views are not logged.

**Lead status flow**
```
new → classified ─┬─ routine   → auto_replied ──────────────────────→ done
                  └─ sensitive → acknowledged → awaiting_approval
                                                 ├─ approve/edit → replied → done
                                                 └─ reject (handled by phone) → done
```

`ai_body` vs `body` yields "% drafts approved unchanged"; `created_at` vs `sent_at` yields reply latency. The dashboard shows both.

### 5.1 Staff authentication
- Accounts managed via CLI only: `create-user`, `reset-password`, `disable-user`. No self-registration, no email reset flow.
- **One role** — all staff can view, approve, edit, reject, resend.
- Passwords hashed with **argon2**; session = signed cookie, valid **8 hours** (one workday).
- **Lockout:** 5 failed attempts → account locked **15 minutes**.
- Staff **edits** to drafts run `guardrails` and show **warnings, never blocks**; warnings shown are stored in `audit_log.details`.

### 5.2 Approval follow-up
- Dashboard highlights drafts waiting longer than `APPROVAL_WARN_HOURS` (default 4).
- Layer 6: notification email to staff for each sensitive lead + **end-of-day reminder** listing drafts still waiting. Links in emails open the draft and **require login**.

## 6. DSGVO handling (lightweight, from day one)
- Data minimization: phone optional; only **redacted message text** is sent to Anthropic — never name, email or phone fields.
- Mandatory consent checkbox; `consent_at` stored. Form rejects submissions without consent.
- Retention job anonymizes personal fields (`name`, `email`, `phone`, `message`) after `RETENTION_DAYS` (default 90); statistics and audit log (which references IDs, not personal data) retained.
- Audit log provides accountability for who sent what to which patient.
- README section listing data processors (Anthropic, email provider, host) and the DPAs/AVVs a real client deployment would require, plus the names-in-free-text limitation. Legal points are orientation, not legal advice; Anthropic's current DPA/data-handling terms to be checked before any real client use.

## 7. Error handling
| Failure | Behavior |
|---|---|
| Any AI call fails | Lead already saved **before** AI calls; **retry up to 3 times** (4 attempts total) with exponential backoff (1s, 2s, 4s), each attempt logged; then safety default (sensitive + manual review) |
| Safe-info paragraph fails or fails guardrails | Dropped; template acknowledgment sent alone |
| Send call returns an error (provider unreachable, connection refused, auth error, 5xx) | Nothing left our system, so resending is safe: **retry up to 3 times** with exponential backoff (1s, 2s, 4s). If all fail → reply `failed` with `error`; dashboard shows a **Resend** button |
| Send call times out (no answer) | Outcome unknown, so the email may already have been sent. **No automatic retry** (avoids double emails) → reply `failed` with `error = "timeout: may have been sent"`; staff decide via **Resend** |
| Provider accepted the email | Reply `sent`, final. **Never resent automatically** |
| Duplicate submission (same email + message within 2 min) | Ignored, returns success to the form |
| Missing consent / invalid email | Form validation error, nothing stored |
| Login: wrong password | Increment `failed_attempts`; at 5 → locked 15 min; generic error message (no hint whether the user exists) |

## 8. Testing & evals
- **Unit tests (pytest):** redactor, router decisions, forced-sensitive categories, unsupported-language path, emergency variant by opening hours/holidays, status transitions, guardrails, retention, duplicate detection, send retry policy (error → retried up to 3×; timeout → not retried; accepted → never resent), auth (hashing, session expiry, lockout), audit entries. LLM and sender mocked.
- **Classifier eval set:** ~40 fake inquiries (German + English, tricky edge cases such as a routine-sounding message mentioning pain) **+ 5 prompt-injection attempts** (must classify as sensitive). Claude generates inquiries and suggested labels; **Tariq reviews and corrects every label** before it counts. `eval_classifier.py` reports category accuracy, risk accuracy, and **sensitive→routine misclassifications — target: 0**.
- **Drafter eval:** every generated auto-reply and safe-info paragraph for the eval set passes `guardrails`; manual read of a sample, notes recorded in README.
- **End-to-end check:** routine lead → auto-reply in Mailpit; sensitive lead → acknowledgment in Mailpit + draft in dashboard; staff login → approve → reply in Mailpit + audit entry; Turkish-language lead → DE+EN acknowledgment, flagged in dashboard, no draft.

## 9. Build layers
| # | Layer | Concepts learned | Done when |
|---|---|---|---|
| 1 | Raw Claude call script | API keys, messages, tokens, cost | Script answers a sample inquiry, prints token cost |
| 2 | Redactor + classifier + eval set | Structured output, Pydantic, evals, data minimization | Eval runs; 0 sensitive→routine; all injection cases sensitive |
| 3 | Practice config + templates + drafter + guardrails | Context prompting, guardrails, deterministic vs AI logic | All eval-set auto-replies/safe-info paragraphs pass guardrails; emergency variants correct by time |
| 4 | FastAPI + Postgres + auth + audit + dashboard + Mailpit + demo site | APIs, DB, sessions/password hashing, HTMX, Docker Compose | End-to-end check passes via direct form → API |
| 5 | n8n intake + sending | Webhooks, n8n flows, swapping implementations | End-to-end check passes via n8n; flows exported to `n8n/` |
| 6 | Staff notification + end-of-day reminder | Notifications, scheduled jobs | Sensitive lead triggers staff email; link opens draft after login; reminder lists waiting drafts |

## 10. Repo layout
```
shared/
  llm.py
track-a-dental/
  a1-lead-responder/
    README.md            # how to run, ROI math, DSGVO notes, eval results
    docker-compose.yml   # api, postgres, mailpit, n8n
    app/                 # FastAPI: redactor, classifier, drafter, templates, practice, guardrails,
                         # router, repository, sender, auth, audit, dashboard, retention, cli
    data/                # faq.md, practice.yaml (hours, holidays, phones, Doctolib), eval_inquiries.jsonl
    site/                # static demo website
    n8n/                 # exported flow JSON
    scripts/             # layer-1 script, eval_classifier.py
    tests/
```

## 11. Definition of done
All six layers working locally via `docker compose up`; tests pass; eval reports 0 sensitive→routine and all injection cases sensitive; README contains run instructions, ROI math, eval results and DSGVO notes; FAQ reviewed by Tariq; a 2-minute demo video recorded.

## 12. Out of scope (A1)
Spam protection (honeypot, rate limiting, captcha — deferred) · user-management UI and roles (CLI only, one role) · email password-reset flow · delivery/bounce tracking via provider webhooks (possible later addition; never triggers a resend) · read/open tracking (unreliable and requires consent under DSGVO/TDDDG) · AI drafts in languages other than DE/EN · cloud deployment · WhatsApp and inbound-email adapters · RAG / vector DB (A2) · Doctolib integration beyond linking · review handling (A3) · multi-practice / multi-tenant support.

## 13. Autonomy decision (added 2026-10-22, retroactive)
**Workflow, not an agent.** Our code fixes the steps (redact, classify, decide, write, check) and the AI fills individual steps; the model never chooses the next step or a tool. Why: every step is known in advance, and the send/no-send decision must be deterministic and testable (`router.py`, `guardrails.py`). Evidence from the build: twice a fixed template beat an AI call (acknowledgments, emergency numbers), and a keyword backstop was needed because the model's judgment is not guaranteed. What would change the decision: a need for open-ended lookups (for example order status), which belongs to Track B (B2), not here. Safety type: A (customer-facing), see `devsetup/safety-patterns.md`.

## 14. Layer 4 failure modes (pre-mortem, plan mode of `/edge-case-probe`, 2026-10-23)
Written before building layer 4. Status: **design** = handled by the design below, **test** = needs a test in the owning part, **parked** = consciously not now. Parts: 4a data, 4b intake API and sender, 4c auth and audit, 4d dashboard, 4e demo site and end-to-end.

| # | Risk | What happens / what stops it | Part | Status |
|---|---|---|---|---|
| 1 | Two identical submissions race past the duplicate check | Duplicate check and insert run atomically under a Postgres advisory lock | 4a | test |
| 2 | Hostile text breaks the database (NUL byte, SQL quotes, emoji, huge fields) | NUL stripped, parameterized SQL only, `CHECK` length limits as a last defence | 4a | test |
| 3 | Personal data survives retention (reply bodies repeat the name, ids only half cleaned) | Anonymization also blanks `replies.body`/`ai_body`; an `edited` flag keeps the "approved unchanged" statistic; idempotent; exact 90-day boundary tested | 4a | test |
| 4 | Naive or wrong-timezone timestamps | Only timezone-aware datetimes accepted; `timestamptz` columns | 4a | test |
| 5 | Database down or slow when a lead arrives | Intake returns an error to the form; no half-saved state; nothing sent | 4b | test |
| 6 | Lead saved, then the process dies mid-pipeline | Status stays `new`/`classified`; a startup sweep re-queues leads with no reply after N minutes | 4b | test |
| 7 | Any exception (not only LLMError) in the pipeline leaves the patient without a response | Handler wraps `process_inquiry`; any exception sends the route-1 acknowledgment and flags the lead | 4b | test |
| 8 | The "instant" acknowledgment waits for slow AI calls (worst case ~13 min) | Send the fixed acknowledgment first, then do the AI calls, with an overall deadline | 4b | test |
| 9 | Form abused: huge body, no consent, invalid email, hostile name, flood from one source | Size caps, consent and email validation, `sanitize_name`, duplicate window, per-IP and per-email rate limit | 4b | test |
| 10 | The form becomes a mail relay (stranger's address + attacker text) | Reply goes only to the form's email address, text is guardrailed final text, rate limit per address and per day | 4b | test |
| 11 | Email sent twice, or never recorded as sent | Typed sender result (`accepted`/`failed`/`timeout`), no auto-retry after a timeout, `sent` is final (spec §7) | 4b | test |
| 12 | Login brute force, user enumeration, stolen session | argon2, lockout 5/15 min, generic error, signed cookie 8 h, secure flags | 4c | test |
| 13 | A staff action is not attributable | Every approve/edit/reject/resend/login writes an audit row in the same transaction | 4c | test |
| 14 | XSS or injection through patient text on the dashboard | Jinja autoescape stays ON, no `|safe`, CSP header | 4d | test |
| 15 | Staff approve an edited draft with a link or number the AI never wrote | Guardrail warnings are shown and stored in `audit_log.details` (warn, never block, spec §5.1) | 4d | design |
| 16 | Two staff edit or send the same draft at once | Status check inside the transaction (only `drafted` can be approved) | 4d | test |
| 17 | A sensitive lead waits unnoticed | Sensitive queue first, drafts over `APPROVAL_WARN_HOURS` highlighted, route-1/2 leads flagged `needs_manual_review` | 4d | design |
| 18 | Demo site posts to the wrong place or leaks the API key | Static page only knows the form URL; no secrets in the page | 4e | design |
| 19 | Cost abuse (unlimited AI calls from one actor) | Rate limits (#9) plus a daily AI call cap per instance | 4b | parked until a client deployment |
| 20 | Redactor, guardrail and keyword-net gaps from the attack report | See "Parked" in `REVIEW.md` | n/a | parked |

