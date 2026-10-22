---
name: run-evals
description: Re-run the A1 lead-responder classifier and drafter evals against the live Anthropic API and record the scores in the README. Use after a prompt, model, router, guardrail or redactor change. It costs real money, so it always asks Tariq first.
---

# Run the evals (asks first, costs money)

## Steps
1. **Say why and what it costs.** One or two lines: what changed since the last recorded scores, and the typical cost: classifier about $0.06, drafter about $0.45 with Sonnet (about $0.19 with Haiku). Check the README "Eval results" for the last numbers.
2. **Ask with AskUserQuestion** which to run: both, classifier only, drafter only, or skip. Do not run anything before the answer.
3. **Run** from the repo root (use `timeout`; each takes a few minutes at most):
   - `uv run python -m lead_responder.scripts.eval_classifier`
   - `uv run python -m lead_responder.scripts.eval_drafter`
   - To compare another model: `DRAFTER_MODEL=<id> DRAFTER_EFFORT= uv run python -m lead_responder.scripts.eval_drafter` (empty effort means none).
4. **Compare with the previous numbers** and call out any regression plainly. The targets: sensitive classified as routine = 0, injection failures = 0, auto-replies rejected by guardrails = 0.
5. **Record only after Tariq agrees:** update the README "Eval results" section (date, models, cost, scores; mark labels as provisional until Tariq has reviewed them) and `devsetup/model-choice.md` if a model decision changed. Commit.
6. **Remind Tariq to read the drafts** in `eval_runs/drafts-*.md` (the header names the model). Automatic checks cannot judge tone or rule-following: two models passed every check and still differed.

## Rules
- Run only in the main session with Tariq, never from a subagent (a subagent cannot ask him, and this spends his API credit).
- Never run the evals without the answer from step 2.
- The key lives in `.env`; never print it or put it in a file.
- Scores are only valid when the run says "Run valid" (all cases got an AI answer).
