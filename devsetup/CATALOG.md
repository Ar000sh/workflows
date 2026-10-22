# Dev setup catalog

> Everything we add to the setup, why, and whether it earned its place. Add a row when something is added; revisit rows that never helped. Live config is in `.claude/`, templates and notes are here.

| Added | Item | Kind | Why (what hurt) | Evidence it helped |
|---|---|---|---|---|
| 2026-10-22 | `.claude/hooks/guard.py` (PreToolUse on Write/Edit/Bash) | hook | The API key almost went into `.env.example` | Blocked its own test command on first use; 20 tests in `.claude/hooks/test_hooks.py` |
| 2026-10-22 | `.githooks/pre-commit` + `.claude/hooks/precommit.py` | git hook | Commits could carry a key or a broken suite; tests run in seconds | Runs for Tariq's commits too. Docs-only commits skip tests; a timeout asks Tariq to change the rule. **Enable once per clone:** `git config core.hooksPath .githooks` |
| 2026-10-22 | `.claude/skills/edge-case-probe` | skill | 100% coverage hid a regex slowdown, a prompt-tag break-out and README drift | Found 2 real bugs in A1 in one pass |
| 2026-10-22 | `.claude/skills/run-evals` | skill | Evals must be re-run after every prompt/model/router change; they cost money | New, not yet used |
| 2026-10-22 | `devsetup/model-choice.md` | template | Choosing Haiku vs Sonnet needs evidence, not guesses | Haiku vs Sonnet drafter experiment recorded |
| 2026-10-22 | `.gitattributes` (`eol=lf`) | config | Windows line-ending warnings on every commit | New |
| 2026-10-22 | `.python-version` (3.12) | config | uv picked Python 3.15 and compiled Rust for minutes | Tests run in seconds |

## Not built, on purpose
- **Custom review agent:** the built-in `/code-review` covers it.
- **Runtime Agent SDK skills / subagents in A1:** the code already knows the language and the steps; revisit in Track C.

## Candidates (decide when the need shows up)
- `devsetup/safety-patterns.md`: reusable AI-in-production checklist (see discussion in the 2026-10-22 retrospective).
- `/fewer-permission-prompts` allowlist once the routine commands are stable.
