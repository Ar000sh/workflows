# Dev setup catalog

> Everything we add to the setup, why, and whether it earned its place. Add a row when something is added; revisit rows that never helped. Live config is in `.claude/`, templates and notes are here.

| Added | Item | Kind | Why (what hurt) | Evidence it helped |
|---|---|---|---|---|
| 2026-10-22 | `.claude/hooks/guard.py` (PreToolUse on Write/Edit/Bash) | hook | The API key almost went into `.env.example` | Blocked its own test command on first use; 20 tests in `.claude/hooks/test_hooks.py` |
| 2026-10-22 | `.githooks/pre-commit` + `.claude/hooks/precommit.py` | git hook | Commits could carry a key or a broken suite; tests run in seconds | Runs for Tariq's commits too. Docs-only commits skip tests; a timeout asks Tariq to change the rule. **Enable once per clone:** `git config core.hooksPath .githooks` |
| 2026-10-22 | `.claude/skills/edge-case-probe` (plan mode + attack mode with `attacker-prompt.md`) | skill + fresh subagent | 100% coverage hid a regex slowdown, a prompt-tag break-out and README drift; the author probing their own code only finds what they can think of | Same-context probe found 2 real bugs in A1. Attack mode is new: record in a follow-up what the fresh attacker finds that we missed |
| 2026-10-22 | `.claude/skills/run-evals` | skill | Evals must be re-run after every prompt/model/router change; they cost money | New, not yet used |
| 2026-10-23 | `.claude/skills/retro` (manual only) + `devsetup/retro-log.md` | skill | We did a 14-item retrospective by hand and want to repeat it whenever a layer ends or something goes badly, including pruning the setup | New; first use is the next layer |
| 2026-10-22 | `devsetup/model-choice.md` | template | Choosing Haiku vs Sonnet needs evidence, not guesses | Haiku vs Sonnet drafter experiment recorded |
| 2026-10-22 | `.gitattributes` (`eol=lf`) | config | Windows line-ending warnings on every commit | New |
| 2026-10-22 | `.python-version` (3.12) | config | uv picked Python 3.15 and compiled Rust for minutes | Tests run in seconds |
| 2026-10-22 | `devsetup/safety-patterns.md` | checklist | A1's safety design should be reusable, but only where it applies | Type A verified in A1; types B and C are marked as hypotheses until Track C |
| 2026-10-22 | `devsetup/spec-template.md` | template | Every spec should state autonomy (workflow vs agent), safety type, models and evals up front | New; A1 spec got a retroactive section |

## Custom agents (subagents): not needed so far, not ruled out
A1 had no need. This is a per-project, per-task question, never a standing rule. A custom agent (a role with its own prompt, tools, model and separate context, kept in `.claude/agents/`) pays off when:
1. **Isolation:** the work would flood the main conversation (reading many files, long logs) and only the conclusion matters.
2. **Parallel, independent work:** tasks that do not depend on each other.
3. **Different model or cost:** cheap model for bulk reading, strong model for judgment.
4. **Restricted tools:** a read-only reviewer or researcher that cannot write.
5. **A role used again and again,** with stable instructions.
Ask this at design time and record the answer. For agents inside a client product, the answer goes in the spec's "Autonomy decision". For the built-in `/code-review`, use it first; build our own only if it falls short.

**Candidate worth trying:** an LLM judge that reads eval drafts and flags broken soft rules (for example "repeated the emergency numbers"), because the automatic checks missed exactly that. Test it against what Tariq finds by hand before trusting it.

## Candidates (decide when the need shows up)
- `/fewer-permission-prompts` allowlist once the routine commands are stable.
