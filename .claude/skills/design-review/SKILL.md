---
name: design-review
description: Independent complexity and data-model review of a draft spec BEFORE the plan is written (or of built code, when complexity is hurting). Dispatches a fresh read-only agent on a strong model that has not seen our reasoning, to separate inherent complexity from accidental complexity (a weak data model), name over-engineering, and say whether to refactor. Use after the spec is drafted and /edge-case-probe plan mode is done, for any project with status columns/state machines, several writers, or money/irreversible actions. Skip for simple CRUD or a single pure function.
---

# Design review

Origin (A1 layer 4b, 2026-10-25): after 4b was built and merged, an independent Opus review found two real design flaws (system replies and staff drafts shared one status, so every send path re-checked approval in code; each repository call committed on its own, so a result was written in five commits) and traced about 40% of the special cases to them. We would have saved a refactor by running that review on the spec. This skill does that earlier.

## When
| Situation | Use |
|---|---|
| Spec drafted, plan not written yet, project has state machines, several writers, or irreversible actions (email, payment, delete) | **Yes**, after `/edge-case-probe` plan mode |
| Built code feels over-checked ("too many special cases") | **Yes**, same prompt, pointed at the code |
| Simple CRUD, a single pure function, a prompt-only change | No |

It is different from `/edge-case-probe` (which hunts failures) and from `/code-review` (which hunts bugs in a diff): this asks whether the design itself creates the failures.

## Steps (main session)
1. **Prepare the brief** from `reviewer-prompt.md` in this folder: fill in the spec path, the files that define the data model and status tables, the files with the business rules, the user's worry in one sentence, and the project's scale (volume, number of developers). Do NOT paste our reasoning, our decisions' justifications, or what we think is wrong: independence is the point.
2. **Tell the user in one line** that an independent read-only agent is reviewing the design and roughly what it costs (the A1 run: about 135k tokens, under 3 minutes).
3. **Dispatch** one agent (`general-purpose`, strongest model; read-only: it may not edit, run migrations, or spend API money).
4. **Triage in the main context.** Verify each claimed file:line yourself before believing it. Overrule claims the code contradicts, and say why (in A1 the review called a flag redundant that was the only signal after a crash). Separate: (a) real flaws, (b) over-engineering for the stated scale, (c) complexity that is justified, do not touch.
5. **Present to the user:** diagnosis, up to three refactor options from smallest to largest (what disappears, what is new, size, risk, migration), and one recommendation. The user decides. Record the decision and the reason in the spec's decisions table and in `PROJECT.md`.
6. Log in `devsetup/CATALOG.md` what the review found that we would have missed.

## Rules
- The reviewer never edits files and never spends money. Probes use fakes.
- A review that says "leave it as is" is a valid and useful result; do not hunt for problems to justify the run.
- Never write regexes or backslashes through bash heredocs; run slow commands under `timeout`.
