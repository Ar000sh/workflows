# Retro log

> One entry per `/retro`. Newest first. Records what we found, where each finding went, and what we decided not to do, so the next retro can check whether it helped.

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

**Follow-ups:** the first fresh-agent attack on A1 (what did it find that we missed?), and checking after a few commits whether the pre-commit gate felt too slow.
