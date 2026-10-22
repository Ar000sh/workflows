# Model choice: which model, effort and why

> Template and log for choosing models per task. One row per task; add evidence when we measure. Used in our own work and in client builds (Haiku classifies, Sonnet writes, etc.).

## How to decide (checklist)
1. Does a wrong answer reach a person without review? If yes, favour the stronger model and add code guardrails.
2. Is the output a fixed shape (labels, JSON)? Use structured output; a small model is usually enough.
3. Is it free text with rules (tone, "never say X")? Instruction following matters; test the small model on the rules, not just on fluency.
4. Check cost per item, not per token: at ~1 cent per inquiry the model price is rarely the deciding factor.
5. Measure with an eval on fixed cases, and read the outputs yourself.

## A1 lead responder (measured 2026-10-22, 45 eval inquiries)

| Task | Model | Effort | Result | Decision |
|---|---|---|---|---|
| Classify inquiry (structured output) | claude-haiku-4-5 | n/a | category 44/45, risk 45/45, 0 sensitive→routine, 0 injection failures, $0.057 per 45 | **Haiku**: fixed shape, safety enforced by router + keyword net |
| Auto-reply (no human review) | claude-sonnet-5-5 | low | 18/18 pass guardrails, facts from the FAQ, tone good, $0.44 for the whole drafter run | **Sonnet**: the only text sent unreviewed |
| Staff draft (human reviews) | claude-sonnet-5-5 | low | follows the "no emergency numbers, no medical advice" rules | **Sonnet** |
| Same drafter run with Haiku | claude-haiku-4-5 | none | 18/18 pass guardrails too, $0.19 (2.4x cheaper) but see below | not adopted |

### Why Haiku was not adopted for the drafter
Both models pass every automatic check, so the guardrails could not tell them apart. Reading the drafts did:
- **Pain drafts:** Haiku repeated the phone number and the Notdienst advice and promised "we will arrange an urgent appointment", against the explicit rule "do not repeat emergency numbers". Sonnet followed it.
- **Formatting:** Haiku put markdown bold (`**...**`) into plain-text emails.
- **Over-claiming:** Haiku wrote "viele Kassen wie die AOK geben einen Zuschuss" where the FAQ only says "viele Kassen" (it took AOK from the patient's message).
- **Cost difference:** about $0.26 per 45 inquiries, roughly 0.6 cents per inquiry. Not worth the extra review risk.

### Open experiments
- Haiku for `safe_info` only (short paragraph inside a template, guardrail-checked).
- Context instead of model size: per-language style guides and example replies, then re-test Haiku on the pain drafts.
- Sonnet effort `low` vs none vs higher on the staff draft.

## Reading the numbers
`eval_drafter` writes `eval_runs/drafts-*.md`; since 2026-10-22 its header records the drafter model and effort. Override per run: `DRAFTER_MODEL=... DRAFTER_EFFORT= uv run python -m lead_responder.scripts.eval_drafter` (an empty effort means none).
