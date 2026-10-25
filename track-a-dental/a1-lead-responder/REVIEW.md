# A1 review index (Tariq's checklist)

> Each project keeps its own `REVIEW.md` next to its README. This file is the **index**: where we are, what is open, and which document to open for each layer. The details live in one file per layer under `review/`, so you read one layer at a time. Update this file whenever a layer's review starts, finishes or changes status.
> Last updated: 2026-10-25 (split into one file per layer)

## One file per layer
Each layer file starts with an overview (what was built, how the parts fit, a table of the public methods where there is one), then the review checklist, decisions to make, and a Parked list.

| Layer | File | Status of your review |
|---|---|---|
| 1-3 Core logic (redactor, classifier, router, drafter, guardrails, pipeline) | [review/layers-1-3.md](review/layers-1-3.md) | Mostly done; a few open boxes (try_inquiry run, effort experiment, context experiment). Redactor hardening and the 2026-10-23 attack report are at the bottom |
| 4a Data foundation (Postgres, models, Alembic, repository, retention) | [review/4a-data-foundation.md](review/4a-data-foundation.md) | Merged into `main` 2026-10-24; steps 3, 6, 7 were not walked through. **"Carry into the 4b/4c/4d plans" is read before writing the 4c and 4d specs** |
| 4b Intake API and sender | [review/4b-intake-sender.md](review/4b-intake-sender.md) | Built on branch `a1-4b-intake-sender`; review open; attack pass not run yet. Includes the 11 build rulings, the go-live gate and "Carry into the 4c/4d plans" |
| Status flows (diagrams of lead and reply statuses, per scenario, the sweep) | [review/status-flows.md](review/status-flows.md) | Reference for 4b step 2; keep in step with `LEAD_TRANSITIONS` / `REPLY_TRANSITIONS` |
| 4c, 4d, 4e | (not built) | Each gets its own `review/4x-*.md` when it is built |

## Where we are
- Layers 1-3 built; live eval scores are in the README. Python pinned to 3.12 (`.python-version`).
- Layer 4a merged into `main`. Layer 4b built on its branch, 544 tests pass; its review and the attack pass are open before merging.
- No staff login, audit log, dashboard or n8n yet (4c to 4e).

## Open decisions for Tariq
- [ ] Monthly API budget (suggested $20 while learning); record in PROJECT.md Decisions log.
- [ ] Keep `.agents/` and `skills-lock.json` (grilling skills installed for another tool)?
- [ ] Add a "what enters the dev setup?" step to the definition of done?
- [ ] Pre-commit gate close to its 90 s limit (details in `review/4b-intake-sender.md`, "to decide or confirm").

## Convention for new layers
Create `review/<layer>-<name>.md` with: a one-paragraph header linking back here, the overview, the review checklist, "to decide or confirm", rulings Claude took on your behalf, go-live gate (if any), "Carry into the next plans", Parked, and attack-probe results. Add a row to the table above. Never add layer content to this index.
