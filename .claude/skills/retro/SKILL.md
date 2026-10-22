---
name: retro
description: Retrospective on the current work session. Collects what hurt, what worked and what Tariq kept asking about, sorts each finding into where it belongs (CLAUDE.md rule, hook, skill, template, agent, or nothing), challenges whether it applies to every project, and proposes dev-setup improvements for the next iteration. Run manually, when a layer or project is finished or when something went badly.
disable-model-invocation: true
---

# Retro: improve the setup, not just the code

Origin: the 2026-10-22 A1 review produced 14 findings that became hooks, skills, rules and templates (see `devsetup/retro-log.md`). This skill repeats that process on demand. Optional argument: a focus area ("tests", "teaching", "cost", ...).

**Nothing is built until Tariq has answered item by item.** Reading the repo and the conversation is fine; creating hooks, skills or rules waits for his choices.

## 1. Collect (from the conversation and the repo)
Read the session, `git log` since the last retro entry in `devsetup/retro-log.md`, the project's `REVIEW.md` (Parked list), and `devsetup/CATALOG.md`. Look for:
- **What hurt:** bugs found late, rework, wrong assumptions, doc/code drift, tool trouble, commands that hung or broke, near misses (a secret almost committed).
- **What worked:** things that caught problems early, which we should do again on purpose.
- **What Tariq asked or corrected repeatedly:** each repeated question points to a teaching gap or an unclear rule; each correction is a preference to keep.
- **Repeated manual steps:** the same procedure done three times is a skill candidate.
- **Decisions made in chat** that are not yet in `PROJECT.md`.

## 2. Sort each finding to a destination
| Destination | Use when |
|---|---|
| **Hook** | Must happen every time, with no judgment (blocking a secret, running tests before a commit). Must have its own tests. |
| **Skill** (slash command) | A repeatable procedure that needs judgment (edge-case probe, run evals, this retro). |
| **CLAUDE.md rule** | A principle or habit for how we work. Keep it one line; delete rules that stopped mattering. |
| **Template / checklist** | A reusable document (spec template, safety patterns, model-choice). |
| **Custom agent** | Only if it meets the criteria in `devsetup/CATALOG.md` (isolation, parallel work, different model/cost, restricted tools, a role used often). |
| **Per-project `REVIEW.md` / Parked** | Good idea, not now. |
| **Nothing** | One-off, or already covered. Say so; not everything deserves a mechanism. |

## 3. Challenge every item (Tariq's standing concern: nothing is a blanket rule)
For each finding answer in one line:
- **Scope:** does it apply to every project, or only some types (customer-facing, agent, internal)? State the type.
- **Evidence:** what actually happened that justifies it? No evidence means a hypothesis; label it so.
- **Cost:** time per run, maintenance, noise, money. A check that is annoying gets bypassed.
- **Now or later:** build now, or wait until the need shows up again?
- **Existing coverage:** is there already a hook, skill, rule or built-in command that does this?

## 4. Also review the setup itself
- **Pruning:** read `devsetup/CATALOG.md`. For each entry, did it help since it was added? Propose removing or shrinking anything that never fired or only added friction. Check `CLAUDE.md` for rules that are stale, duplicated or too long.
- **Models, effort and context:** where did we use a stronger model or more context than needed, or a weaker one than the task deserved? What context was loaded but never used, and what was missing that we had to re-explain? Feed results into `devsetup/model-choice.md`.
- **Process:** where did the edge-case plan/attack, the evals or the review catch things, and where did a problem slip through that an earlier step should have caught?

## 5. Present, then ask once
Show a table: finding, evidence, destination, scope, cost, recommendation (build now / later / drop). Keep it to the findings that matter (aim for 10 or fewer) and offer the rest. End with one question: which items to build, change or drop. Tariq usually answers by item number.

## 6. Build what he approved
- Hooks get tests, are piped-tested before being trusted, and log to `devsetup/CATALOG.md`.
- Skills go in `.claude/skills/<name>/SKILL.md` (add a `!` exception in `.gitignore` for each new skill folder, since symlinked grill skills are ignored).
- Add a row to `devsetup/CATALOG.md` and a Decisions log row in `PROJECT.md`.
- Append an entry to `devsetup/retro-log.md`: date, trigger, findings and what was built, held or dropped.
- Run the suite and commit (the pre-commit hook will gate it).

## Rules
- Do not spawn agents for the retro itself; it is a conversation with Tariq.
- Never promise a mechanism will "always" work: state what it covers and what it cannot catch.
- Keep outputs short and in plain language; Tariq is learning, so explain the why of each recommendation in one sentence.
