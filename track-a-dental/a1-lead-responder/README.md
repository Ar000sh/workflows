# A1 — BrightSmile Lead Responder

Website inquiries for a (fictional) dental practice in Hannover: routine questions get an
instant AI reply; sensitive ones get an instant acknowledgment plus an AI draft for staff.
Spec: `docs/superpowers/specs/2026-10-05-a1-lead-responder-design.md`

**Status:** layers 1–3 (core logic, no web/database yet).

## Setup
Requires [uv](https://docs.astral.sh/uv/) to be installed.
```bash
uv sync
cp .env.example .env        # Git Bash; in PowerShell/cmd use: copy .env.example .env
# then paste your ANTHROPIC_API_KEY into .env
```

## Run
Run all commands from the repo root.

| What | Command |
|---|---|
| Unit tests (no API calls) | `uv run pytest` |
| Layer 1: first Claude call | `uv run python -m lead_responder.scripts.hello_claude` |
| One inquiry end-to-end | `uv run python -m lead_responder.scripts.try_inquiry --name Anna --message "Haben Sie samstags geöffnet?"` |
| Classifier eval | `uv run python -m lead_responder.scripts.eval_classifier` |
| Drafter eval | `uv run python -m lead_responder.scripts.eval_drafter` |

## How an inquiry flows
1. **Redact** emails and phone numbers → only the message text goes to Claude.
2. **Classify** (Haiku, structured output) → category, risk, urgency, language.
3. **Route** (plain Python) → auto-reply, or acknowledgment + staff draft. A keyword safety net (pain, bleeding, swelling, Notfall, 112, ...) forces the sensitive route even if the classifier said routine.
4. **Write** texts (Sonnet) from `data/faq.md`; acknowledgments are fixed templates plus an optional AI safe-info paragraph (guardrail-checked).
5. **Guardrails** check every AI text; a failing auto-reply goes to staff instead.

## Known limitation
A refusal or `max_tokens` stop is **not** retried (the same answer would come back): the call fails at once and the lead falls back to human review. Network errors, 429/5xx and invalid structured output are retried (up to 4 attempts).

## Eval results
Run on 2026-10-22 against the live API. **Provisional:** the eval labels have not yet been reviewed by the practice owner (Tariq), so the scores only count once they are.

Classifier (`eval_classifier`, 45 inquiries incl. 5 prompt-injection cases), model `claude-haiku-4-5`, cost $0.057:
- Category accuracy 44/45, risk accuracy 45/45, language accuracy 45/45.
- Sensitive classified as routine: **0** (target 0). Injection failures: **0** (target 0). Errors: 0.
- The one miss is `i05` (expected `medical_question`). Haiku answers `pain_emergency` or `other` from run to run; both are sensitive, so routing is the same. A label to review, not a safety problem.

Drafter (`eval_drafter`), models `claude-sonnet-5-5` (effort low) + Haiku classifier, cost $0.44:
- 18 auto-replies produced, **0** rejected by guardrails; **0** safe-info paragraphs rejected; 0 API errors, 0 classifier failures.
- Manual review findings: **not done yet** (read the generated `eval_runs/drafts-*.md` first; the folder is gitignored).

Re-run after any prompt, model or router change:
`uv run python -m lead_responder.scripts.eval_classifier` and
`uv run python -m lead_responder.scripts.eval_drafter`.

## DSGVO notes
- Only redacted message text is sent to Anthropic; names, emails and phone fields stay local.
- Known limitation: names, birth dates and street addresses written inside the message text are not removed (see REVIEW.md, "Parked: redactor hardening").
- Processors a real deployment needs a DPA (AVV) with: Anthropic, the email provider, the hosting provider.
- These notes are orientation, not legal advice.
