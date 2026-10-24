# CLAUDE.md

This repo is Tariq's lab and portfolio for learning **AI automation and AI workflows for businesses**, with the goal of freelancing.

**Read the project brief first. It defines the goals, tracks, open questions and decisions:**
@PROJECT.md

## How to work in this repo
- **Teach while building.** Tariq is learning, so explain *why* a design choice was made (briefly) and point out the concept being practiced (e.g. "this is chunking", "this is tool use").
- **Every project must be sellable.** Tie each build to one of the 5 business outcomes in PROJECT.md and note its ROI math in that project's README.
- **Fake but realistic data.** Generate sample data per fictional business inside that track's `data/` folder. Never use real customer data.
- **Reuse `shared/`.** Put LLM, embedding and vector-DB helpers there, not copied into each track.
- **Secrets** go in `.env` (never committed); keep a `.env.example` up to date.
- **n8n flows** are exported as JSON into the track's `n8n/` folder so they are versioned.
- **Definition of done** for a project: runs end-to-end from its README, has a demo script, and RAG bots have an eval set with a recorded score.

## Engineering rules (learned in the A1 review, 2026-10-22)
- **Docs change with the code.** When behaviour changes, update the README, spec and comments in the same commit, and grep for the old term (a README once claimed retries the code never did).
- **Never write regexes or backslashes through bash heredocs or `python -c`.** Escapes get mangled (`\b` turned into a backspace character). Use Write/Edit, and prove each regex with a test.
- **Slow commands run under `timeout`.** The Python version is pinned in `.python-version` (uv once picked 3.15 and compiled Rust for minutes).
- **Tests gate commits.** `.githooks/pre-commit` runs the suite (skipped for docs-only commits, 90 s limit). If it hits the limit, do not bypass it and never use `--no-verify`: ask Tariq whether to change the rule, for example run only the relevant tests.
- **Secrets:** a hook blocks writing `.env` or key literals (`sk-ant-...`). Tariq edits `.env` himself; Claude only touches `.env.example`.
- **Comments teach, briefly.** A few lines above each function or step saying what happens and why (what flows where), in the style of `pipeline.py` and `router.py`. Never the history of a change (that goes in the commit or REVIEW.md). In schema code every column, index and constraint that is not obvious gets a one-line why; every lock says which lock and why.
- **Each project keeps its own `REVIEW.md`** next to its README: what Tariq must review or decide, plus a "Parked" list for ideas we chose not to do now. A layer's review starts with an overview (what was built, how the parts fit, a table of every public method), then the checklist.
- **Edge cases in two moments:** at design time run `/edge-case-probe` in plan mode and fill the spec's "Failure modes" section; before calling a module or layer done run it in attack mode (a fresh subagent that has not seen our reasoning tries to break the code). **After a prompt, model, router or guardrail change, run `/run-evals`** (it asks first, because it costs money).
- **Shared data, two actors (only code where several requests or workers can touch the same rows):** a new lock or conditional write gets a test with two sessions racing, shown to fail when the fix is removed. A lock's strength must match what the method does next (two share locks that then update the same row deadlock). A change to locking or transactions made after the last review gets a fresh-agent attack pass (`/edge-case-probe`, naming concurrency as the focus) before it is called done. Evidence: the A1 4a `FOR SHARE` deadlock, 2026-10-24.
- **New project specs start from `devsetup/spec-template.md`:** it forces an explicit Autonomy decision (workflow or agent, why, how we check it), a Safety type (`devsetup/safety-patterns.md`) and a models/context section. Patterns are matched to the project type, never applied blindly.
- **Dev setup:** log every hook, skill or template we add in `devsetup/CATALOG.md`; model and effort choices go in `devsetup/model-choice.md` with measured evidence.

## Using superpowers workflows with our skills
Checked 2026-10-23: a subagent sees this CLAUDE.md, sees `edge-case-probe` and `run-evals`, and our `guard.py` hook blocks its writes too. It does **not** see `/retro` (manual-only). It cannot ask Tariq questions. So the skills are placed by stage, not handed to every subagent:
- **Before writing a plan:** run `/edge-case-probe` in plan mode and fill the spec's "Failure modes"; the plan's Review Focus comes from it. (Main session.)
- **While executing a plan** (subagent-driven or native): implementers and reviewers follow this file and TDD. When dispatching one, paste the plan's Global Constraints into its prompt, say the pre-commit gate exists and `--no-verify` is forbidden, and tell it to **stop and report, not ask**, if it is blocked.
- **After the last task of each plan part** (for example 4a, 4b): the main session runs `/edge-case-probe` in attack mode (a fresh agent, independent of the implementers), triages, fixes the top 1-3 and parks the rest. Plans do not need to repeat this as a task; this rule is the source.
- **Main session only, never from a subagent:** `/run-evals` (it asks Tariq and spends his API credit) and `/retro` (a conversation with Tariq).
- **At the end of a layer or project:** `/retro`.

## Keeping the brief current
- When a decision is made (in a grilling session or otherwise), add it to **Decisions log** in PROJECT.md and remove it from **Open questions**.
- Do not start building a track while its blocking open questions are unanswered. Grill first.

## Skills in this project
- `/grill-me` (via `grilling`): stress-test the plan. Grill against the **Open questions** in PROJECT.md and write the conclusions back into it.
- `/edge-case-probe`: probe a finished module for edge cases beyond unit tests (`.claude/skills/edge-case-probe`).
- `/retro`: manual-only retrospective: sorts what hurt or worked into hooks, skills, rules or templates, challenges whether each applies everywhere, and prunes the setup (`.claude/skills/retro`; log in `devsetup/retro-log.md`).
- `/run-evals`: re-run the live evals after a change and record the scores; asks before spending money (`.claude/skills/run-evals`).
