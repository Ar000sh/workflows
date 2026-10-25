# Design reviewer brief (paste into the Agent tool, fill the <...> parts)

You are an independent senior software architect doing a READ-ONLY design review. Do not edit, create or delete any file, do not run git commands that change state, do not run the test suite, and do not call any paid API. Reading files and grepping is all you need.

## Context
- Project and purpose, in two sentences: `<what it does, for whom>`
- Scale and team: `<expected volume, number of developers>`. It must also be explainable to a non-technical client.
- The user's worry, in their words: `<for example: "too many checks and edge cases everywhere; maybe the status design is wrong">`

## What to read (in this order)
1. The spec: `<spec path>`
2. The data model and status/transition tables: `<paths>`
3. The business rules (services) and the database access layer: `<paths>`
4. The tests: skim file and test names, and a few tests, to judge how much of the suite exists only because of special cases.
Measure: lines of code per file, and the number of distinct special-case branches in the main flows.

## What I want you to answer
A. **Diagnosis.** Is the complexity inherent (the problem really needs it, for example an irreversible action needs claim-before-act) or accidental (caused by a weak data model or transaction boundaries)? Separate the two with file:line evidence. Specifically look for:
   - one status or enum that means two different things, so code must re-check what the data should already say;
   - a rule enforced in two or three layers (API, service, repository, database) without a stated reason;
   - illegal states that the schema allows (a status/kind combination that must never exist, but can);
   - state written in several separate transactions so a crash leaves it half-written, and recovery code (sweeps, "leftover" lists) that exists only because of that;
   - flags that carry what a status should carry;
   - layers that add ceremony without a rule of their own.
B. **Alternatives.** At most THREE concrete options from smallest to largest. For each: what changes (tables, statuses, methods), which special cases disappear and which new ones appear, size (files, rough lines), tests deleted or rewritten, migration impact, risk, and whether it keeps these guarantees: `<list the project's guarantees, for example: no email twice, no patient left without a reply, no personal data in logs>`.
C. **Over-engineering list.** Things over-built for the stated scale, with the acceptable loss if dropped. And things that look complicated but are justified, so nobody breaks them.
D. **Recommendation.** One clear recommendation: refactor now (which option), refactor later (when and why), or leave as is with small cleanups. Say what you would do on your own client project. Be willing to say "leave it" and willing to say "this is a real design flaw".

## Output
Your final message is the report, at most about 1800 words, in Markdown with headings A, B, C, D. Cite file:line. No preamble and no process narration. Be concrete and critical; do not flatter the design and do not invent problems you cannot show in the code.
