# Retro log

> One entry per `/retro`. Newest first. Records what we found, where each finding went, and what we decided not to do, so the next retro can check whether it helped.

## 2026-10-25: A1 layer 4b (third retro, read from the skill text and run as a conversation)
**Trigger:** 4b built, merged with the attack pass still open, then an independent design review found two real flaws. **Result:** 10 findings, 6 built (1, 2, 3, 4, 5, 8), 4 waiting or dropped.

| # | Finding | Went to | Notes |
|---|---|---|---|
| 1 | A status model that allows illegal states breeds patches (`drafted` meant two things; five patches followed) | spec template section 5 + `edge-case-probe` checklist bullet "Illegal states" | Scope: any project with status columns |
| 2 | Run an independent complexity review at spec time, not after the build | new skill `/design-review` (+ `reviewer-prompt.md`), called from CLAUDE.md "Before writing a plan" | Tariq asked for a skill rather than a loose prompt; skip for simple CRUD |
| 3 | Rulings taken on Tariq's behalf were reviewed too late; three touched statuses or patient-visible results | CLAUDE.md "Who decides what" | Tariq: make it generic so it applies to other projects |
| 4 | No demo script on the real stack when the build ended | CLAUDE.md definition of done + spec template build layers | Real AI call, asks before spending; exists before the review starts |
| 5 | Reviews need flow diagrams; one file per layer | CLAUDE.md review rule (Mermaid), `review/` split, `status-flows.md` | Built during the session |
| 6 | Pre-commit gate: 35-60 s, once 91 s (limit 90) | waiting | Tariq decides after Task 5 of the refactor re-measures |
| 7 | Subagent hygiene: forbid `git stash`, `reset --hard`, `checkout --` in dispatch rules | waiting | Not answered; the refactor plan's Global Constraints already say it |
| 8 | Fresh-agent review beats implementer tests on money and irreversible paths | CATALOG evidence row only | Duplicate email, stranded replies, personal data in logs |
| 9 | Merged with the attack pass and part of the review still open | dropped | One occurrence; PROJECT.md records the open gates |
| 10 | Long sessions (compaction once); Opus use for design review | later | Needs measured data for `model-choice.md` |

**Follow-ups:** at the next layer check (a) whether the demo script existed before the review, (b) whether `/design-review` ran on the spec and what it found, (c) whether any ruling touching a status table or a patient-visible result was taken without asking, (d) the gate time after the refactor (Task 5 of the refactor plan), (e) the previous follow-up "gate under half the limit" failed in 4b (35-60 s, once 91 s).

## 2026-10-24: A1 layer 4a (first run of `/retro`)
**Trigger:** 4a built, reviewed and attacked twice. **Result:** 10 findings, 5 built, 1 waiting for Tariq, 4 dropped.

| # | Finding | Went to | Notes |
|---|---|---|---|
| 1 | A new lock needs a two-session test; strength must match the next write; locking changes after review get an attack pass | CLAUDE.md rule "Shared data, two actors" | Tariq merged findings 1 and 2 (rule instead of a separate checklist) |
| 2 | Stateful-code checklist in `safety-patterns.md` | dropped | Covered by 1 |
| 3 | Concurrency variant of the attacker brief | dropped | The brief used on 2026-10-24 is in the conversation; revisit if the rule gets used |
| 4 | One layer per rule | spec template section 5 | Tariq's correction: business rules belong in a service, the repository is database access. Where the commit lives is a choice (my first wording claimed the service); Tariq chose to keep it in the repository, as in 4a |
| 5 | Comment style | CLAUDE.md, reworded existing rule | Short, why not history, one line per index/constraint, every lock says which and why |
| 6 | Review starts with an overview | CLAUDE.md, REVIEW.md rule | |
| 7 | Decisions made in chat not in PROJECT.md | waiting | Tariq asked for an explanation first |
| 8 | Stop and explain when Tariq is confused | feedback memory | Hypothesis from three occurrences |
| 9 | Commit gate grew from 13 s to 44 s (limit 90 s) | dropped for now | Watch it; the race tests wait one second each |
| 10 | `core.bare` was true once | dropped | One occurrence, no recurrence in about 15 suite runs |

**Follow-ups:** at the next layer, check whether the two-session-test rule was followed, whether the review began from the overview, and whether the gate time is still under half the limit.

## 2026-10-22: A1 core review (first retro, done by hand)
**Trigger:** finishing the layers 1-3 review. **Result:** 14 findings, 11 built, 3 shaped by Tariq's answers.

| # | Finding | Went to | Notes |
|---|---|---|---|
| 1 | 100% coverage hid real bugs (slow regex, tag break-out, README drift) | skill `edge-case-probe` | Later split into plan mode and a fresh-agent attack mode because the author's own probe is biased |
| 2 | Docs drift from code | CLAUDE.md rule | Update docs in the same commit, grep the old term |
| 3 | Backslashes broke in bash heredocs (`\b` became a backspace) | CLAUDE.md rule | Write regexes with Write/Edit and test them |
| 4 | uv picked Python 3.15, Rust build hung | `.python-version` + rule | Run slow commands under `timeout` |
| 5 | API key almost went into `.env.example` | hook `guard.py` | 20 tests |
| 6 | Tests are fast enough to gate commits | git hook `precommit.py` | Docs-only skips; on timeout Claude asks Tariq to change the rule |
| 7 | Evals must be re-run after changes, and cost money | skill `run-evals` | Asks first |
| 8 | Scores did not separate Haiku from Sonnet; reading drafts did | inside `run-evals` (step 6) | Tariq was unsure; kept in the skill only; LLM-judge idea listed as a candidate |
| 9 | Edge-case lists grow without limit | inside `edge-case-probe` (rank, fix top 1-3, park) | Tariq was unsure; kept in the skill only |
| 10 | Safety design that worked in A1 | `devsetup/safety-patterns.md` | Split by project type; agent/internal parts are hypotheses until Track C |
| 11 | Least autonomy | spec template "Autonomy decision" | A check against a baseline, not a blind rule |
| 12 | Per-project REVIEW.md, teaching comments | CLAUDE.md | |
| 13 | CRLF/LF warnings | `.gitattributes` | |
| 14 | Custom agents | not built; criteria in CATALOG | "Not needed so far, not ruled out" |

**Follow-ups:** ~~the first fresh-agent attack on A1~~ done 2026-10-23: 13 findings, 5 fixed, rest parked (see CATALOG and A1 REVIEW.md). Still open: does the pre-commit gate feel too slow after a few commits? Do the stricter guardrails raise the false-alarm rate on real drafts (re-run the drafter eval, `/run-evals`)?
