---
name: edge-case-probe
description: Find edge cases and failure modes in two modes. PLAN mode (at design time, no code) applies the checklist to a spec so problems are caught before building. ATTACK mode (after building) dispatches a fresh subagent that sees only the spec and the public interface and tries to break the code, then triages what it finds. Use plan mode when writing a spec, attack mode before calling a module or layer done, and whenever Tariq asks about edge cases or hardening.
---

# Edge-case probe

100% coverage only shows that every line ran. Origin: in A1 a probe found a regex slowdown on huge input, a prompt-tag break-out and a README that contradicted the code, all with 100% coverage. But that probe ran in the author's own context, so it only tried what the author could think of. Hence two modes and a fresh attacker.

## Pick the mode
| Situation | Mode |
|---|---|
| Writing or reviewing a spec, before code exists | **Plan** |
| A module, layer or API is built and its tests pass | **Attack** |
| Small change to an already-probed module | Neither; add a regression test instead |

## Checklist (used by both modes)
- **Size and time:** huge input (10k, 100k, 1M chars) of junk that almost matches each regex. Is the size limit applied *before* the expensive work?
- **Delimiters and injection:** can input close a tag, fake a system message, or smuggle instructions outside the data block?
- **Empty, whitespace, odd values** for every field: empty, spaces only, very long, newlines, braces, HTML, unicode, wrong language codes.
- **Exact boundaries:** limit and limit+1, opening hours, midnight, holidays.
- **Unreviewed output:** what can the AI or the code send without a human? What stops a wrong text there? Try to make it fail.
- **Failure paths:** what if the API, DB or sender fails halfway? Does the person still get a response? Is any failure silent?
- **Word lists and patterns:** phrases that should trigger and phrases that must not; check against real eval data.
- **Docs vs code:** every claim in the README, spec and comments about behaviour (retries, limits, names) checked against the code.
- **Duplicates, repeats, concurrency (layer 4+):** same request twice, two at once, replays, a retry after a timeout.
- **Money and abuse:** can one visitor trigger unlimited AI calls? Is there a cost cap?

## PLAN mode (design time, no code)
1. Read the draft spec (`devsetup/spec-template.md` shape). Take each component and flow.
2. Walk the checklist against the *design* and write the "Failure modes" section of the spec: per risk, what happens and which layer stops it (code, template, guardrail, human).
3. Mark each risk **handled by design**, **needs a test** (it goes into the plan as a test from the start), or **parked** (with why).
4. Show Tariq the list ranked by impact x likelihood; he decides what is in scope.

## ATTACK mode (after building)
1. **Prepare the brief.** Collect: the spec path, the paths of the public interface files (entry functions and their signatures), how to run the tests, and where the scratchpad is. Do **not** pass our conversation, the list of what we already tested, or our opinions about weak spots. Independence is the point.
2. **Dispatch a fresh subagent** (Agent tool, `general-purpose`) with the prompt in `attacker-prompt.md` (same folder), filling in the paths. Tell Tariq in one line that an independent agent is attacking the code and roughly what it costs in tokens.
3. **Triage the report in the main context.** Reproduce every claimed finding yourself before believing it; drop the ones that do not reproduce. Rank by impact x likelihood. Say which findings the earlier, biased probes did not find.
4. **Fix the top 1-3 test-first** (red, then green), each with a regression test that stays in the suite. **Park the rest** in the project's `REVIEW.md` under "Parked" with why. Do not fix everything.
5. **Report** in four lines: found, fixed, parked, known limits. Add timing numbers for anything slow.
6. Log in `devsetup/CATALOG.md` what the attacker found that we would have missed, so we learn whether the mode earns its cost.

## Rules
- Never write regexes or backslashes through bash heredocs or `python -c`: escapes get mangled. Use Write/Edit and prove regexes with a test.
- Run anything that might hang with `timeout`.
- Probes use fakes (`shared/testing.py`); a real-API check is a separate, user-approved step (see the `run-evals` skill).
- The attacker never edits the repository and never spends API money.
