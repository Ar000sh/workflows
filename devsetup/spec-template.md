# <Project name>: Design Spec

> Copy to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`. Delete hints. Sections 1-3 and 8-10 are the ones the A1 retrospective added.

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

## 6. Data and flows
Data model, what is sent to the AI (minimized), retention.

## 7. Error handling
Per failure: what happens, who is told. "An error may only lead to human review, never to a wrong automatic action" for customer-facing work.

## 8. Safety type
Type A, B or C from `devsetup/safety-patterns.md` (or mixed). List which patterns apply and which do not, and why.

## 9. Models and context
Which model per task, effort, thinking on/off, and the evidence or the experiment planned (`devsetup/model-choice.md`). What goes into the context and how it is selected (FAQ, style guide, examples).

## 10. Testing and evals
Unit tests, eval set (who reviews the labels), safety targets (usually 0), what is read by hand, and the planned `/edge-case-probe` pass.

## 11. Build layers
Layer, concepts learned, done when.

## 12. Definition of done
Runs end-to-end from the README, tests pass, eval scores recorded, README and spec match the code, demo script or video, `REVIEW.md` closed out.

## 13. Out of scope
Explicit list, so ideas have a parking place.
