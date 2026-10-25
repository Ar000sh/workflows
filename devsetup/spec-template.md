# <Project name>: Design Spec

> Copy to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`. Delete hints. Sections 3 and 8-11 are the ones the A1 retrospective added.

## 1. Purpose and business outcome
One of the 5 outcomes in PROJECT.md, the ROI story (hours saved x rate, or leads not lost), the learning goal.

## 2. Context and constraints
Market and legal (DSGVO?), fake data only, tools and hosting, budget.

## 3. Autonomy decision
- **Workflow or agent?** Workflow = our code decides the steps; agent = the model decides the next step in a loop.
- **Why:** which steps are known in advance? Start with the least autonomy that meets the goal; an agent needs a reason (the number of steps or tools depends on the input).
- **How we will check it:** a baseline to compare against (for example a plain workflow on the same eval set) and what result would change the decision.
- If agent: tool allowlist, step/token/cost limits, what needs human approval.

## 4. Decisions
Table: decision, choice, why. Mark which were Tariq's and which are Claude's calls.

## 5. Architecture and components
Diagram, one responsibility per unit, what each depends on.
If the project has an API and a database, say where each rule lives (a common layering): the API validates input; a service holds the business rules; the repository is database access (queries, locks and its own commit: one method = one transaction); the database enforces hard limits. Choose where the commit lives (repository, or a service that commits once over several repository calls = unit of work, needed when several writes must be atomic) and say why. A rule that lives in two layers needs a stated reason.
For every status or enum column: list the allowed moves AND the combinations that must never exist (for example kind x status), and enforce the impossible ones in the database (CHECK). A status that means two things (a staff draft and a system reply both "drafted") forces every caller to re-check, so split it. Several writes that must go together belong in one repository method, one transaction. Then run `/design-review` on this draft (independent agent; skip for simple CRUD) and record its outcome in section 4.

## 6. Data and flows
Data model, what is sent to the AI (minimized), retention.

## 7. Error handling
Per failure: what happens, who is told. "An error may only lead to human review, never to a wrong automatic action" for customer-facing work.

## 8. Failure modes (pre-mortem, written BEFORE building)
Run the checklist of `.claude/skills/edge-case-probe` (plan mode) against this design. One row per risk:

| Risk | What happens | Stopped by (code / template / guardrail / human) | Status: handled by design / needs a test / parked (why) |
|---|---|---|---|

Cover at least: huge or hostile input, delimiter break-out, every failure of an external call, what reaches a person unreviewed, duplicates and retries, cost abuse.

## 9. Safety type
Type A, B or C from `devsetup/safety-patterns.md` (or mixed). List which patterns apply and which do not, and why.

## 10. Models and context
Which model per task, effort, thinking on/off, and the evidence or the experiment planned (`devsetup/model-choice.md`). What goes into the context and how it is selected (FAQ, style guide, examples).

## 11. Testing and evals
Unit tests, eval set (who reviews the labels), safety targets (usually 0), what is read by hand, and and the planned `/edge-case-probe` attack pass by a fresh agent after the build.

## 12. Build layers
Layer, concepts learned, done when. The last task of each layer is the demo script: it runs the real stack (real AI call, real mail path into Mailpit or a sandbox), asks before spending, and prints what happened, so Tariq sees real behaviour before his review.

## 13. Definition of done
Runs end-to-end from the README, tests pass, eval scores recorded, README and spec match the code, demo script (real stack) or video, status diagrams match the code, `REVIEW.md` closed out.

## 14. Out of scope
Explicit list, so ideas have a parking place.
