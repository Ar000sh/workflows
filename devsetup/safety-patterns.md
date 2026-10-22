# Safety patterns for AI automation

> Reusable checklist, grown from A1. **Pick the project type first: the patterns differ.** At each project's design time, state which type it is and confirm the choice in the spec (section "Safety type").

## Which type is this project? (initial guesses, confirm per project)
| Type | Meaning | Projects |
|---|---|---|
| **A. Customer-facing automation** | AI text or decisions reach customers/patients, personal data involved | A1, A3, B1, A2 (chatbot) |
| **B. Agent / tool use** | The AI chooses steps and calls tools; reads untrusted outside content | C1, B2 (order lookup tool) |
| **C. Internal assistant** | Staff only, a human always reads the result | C2, C3 |

A project can be mixed (B2 is A plus B). Use every list that applies.

## Type A: customer-facing automation (verified in A1, 2026-10-22)
1. **Code has the last word.** The decision of what may be sent is plain, tested code (`router.py`, `guardrails.py`), not a prompt.
2. **A failure means a human, never a wrong automatic action.** Classifier or draft failure, a failed guardrail, a too-long message: all end in manual review (`decide(None)`, `downgrade_failed_auto_reply`).
3. **Minimize data before the AI.** Redact contact details and ID numbers (`redactor.py`); send only the message text. Know the limits and write them down (names, birth dates, addresses remain).
4. **Untrusted text is delimited and escaped.** One wrapper for all prompts (`prompting.inquiry_block`); the model is told the content is data. A message must not be able to close the tag.
5. **Strictest checks on unreviewed output.** Only text that goes out without a human gets guardrails plus fixed templates for the critical parts (emergency numbers live in a template, not in AI text).
6. **A deterministic backstop under the classifier for the highest-harm class** (keyword net for pain/injury). It may only make the outcome safer.
7. **Limits before expensive work.** Size limit before regexes or API calls (a 1 MB message once made the redactor crawl).
8. **Never silent.** Events are logged and visible to staff (`EVENT_*`, dashboard flags).
9. **Evals with safety targets of zero** (sensitive classified as routine, injection failures), re-run after every prompt, model or rule change.
10. **Read the outputs, not just the scores.** Automatic checks did not separate two models that behaved differently.

## Type B: agent / tool use (HYPOTHESES, verify in Track C before relying on them)
- Least privilege: an allowlist of tools; read-only where possible; no secrets reachable by the agent.
- Everything fetched from the web or documents is untrusted and can carry instructions (indirect prompt injection).
- Hard limits: steps, tokens, money, time per task.
- Log every tool call and its result.
- Irreversible or outward actions (sending, writing, paying) need human approval or a dry-run first.
- Evals include adversarial pages/documents, and a plain-workflow baseline (see "Autonomy decision" in the spec template).

## Type C: internal assistant (HYPOTHESES)
- Access control: the assistant must not show a user documents they may not read.
- Show sources so staff can check; a human still approves anything that leaves the company.
- Keep the Type A data-minimization rule if the content is personal.

## Update rule
When a project teaches us a new pattern (or disproves one here), change this file in the same commit and note it in `CATALOG.md`.
