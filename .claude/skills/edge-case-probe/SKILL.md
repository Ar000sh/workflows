---
name: edge-case-probe
description: Probe a finished module, pipeline or API for edge cases that unit tests and coverage miss (huge or odd input, delimiter break-out, boundaries, unreviewed output, word lists, docs that contradict the code). Use before calling a module or layer done, and whenever Tariq asks about edge cases or hardening.
---

# Edge-case probe

100% coverage only shows that every line ran. This skill finds what hostile or odd input does. Origin: the A1 probe found a regex slowdown on huge input, a prompt-tag break-out and a README that contradicted the code, all with 100% coverage.

## Steps
1. **Map the unit.** List every input (including form fields like name) and every output that reaches a person *without human review*. Those are the highest risk.
2. **Write a scratch probe script in the scratchpad directory, not the repo.** Use the fakes (`shared/testing.py`: `FakeLLM`, `fake_response`) so the probe costs nothing. Print results; do not assert yet.
3. **Run the checklist** (below) and read the output.
4. **Rank findings** by impact x likelihood. Tell Tariq the ranking.
5. **Fix the top 1-3 test-first** (red, then green), each with a test that stays in the suite. **Park the rest** in the project's `REVIEW.md` under "Parked", with why. Do not try to fix everything.
6. **Report** in four lines: found, fixed, parked, known limits. Include timing numbers for anything slow.

## Checklist
- **Size and time:** huge input (10k, 100k, 1M chars) of junk that almost matches each regex. Time every regex and call. Is the size limit applied *before* the expensive work?
- **Delimiters and injection:** can the input close a tag, fake a system message, or smuggle instructions outside the data block? Is untrusted text always wrapped the same way?
- **Empty, whitespace, odd values** for every field: empty, spaces only, very long, newlines, braces `{}`, HTML, unicode, emoji, wrong language codes.
- **Exact boundaries:** limit and limit+1, open and closing time, midnight, holidays.
- **Unreviewed output:** what can the AI or the code send without a human? What stops a wrong text there (guardrail, template, fallback)? Try to make it fail.
- **Failure paths:** what if the API, DB, or sender fails halfway? Does the user still get a response? Is a failure ever silent?
- **Word lists and patterns:** run phrases that should trigger and phrases that must not. Check the lists against the real eval data.
- **Docs vs code:** grep the README, spec and comments for claims about behaviour (retries, limits, names of tokens) and check each against the code.
- **Duplicates and repeats (layer 4+):** same request twice, two at once, replays.

## Rules
- Never write regexes or backslashes through bash heredocs or `python -c`: escapes get mangled (`\b` became a backspace character once). Write files with Write/Edit and prove regexes with a test.
- Run anything that might hang with `timeout`.
- Probes use fakes; real-API checks are a separate, user-approved step (see the `run-evals` skill).
