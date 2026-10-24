# Dev setup catalog

> Everything we add to the setup, why, and whether it earned its place. Add a row when something is added; revisit rows that never helped. Live config is in `.claude/`, templates and notes are here.

| Added | Item | Kind | Why (what hurt) | Evidence it helped |
|---|---|---|---|---|
| 2026-10-22 | `.claude/hooks/guard.py` (PreToolUse on Write/Edit/Bash) | hook | The API key almost went into `.env.example` | Blocked its own test command on first use; 20 tests in `.claude/hooks/test_hooks.py` |
| 2026-10-22 | `.githooks/pre-commit` + `.claude/hooks/precommit.py` | git hook | Commits could carry a key or a broken suite; tests run in seconds | Runs for Tariq's commits too. Docs-only commits skip tests; a timeout asks Tariq to change the rule. **Enable once per clone:** `git config core.hooksPath .githooks` |
| 2026-10-22 | `.claude/skills/edge-case-probe` (plan mode + attack mode with `attacker-prompt.md`) | skill + fresh subagent | 100% coverage hid a regex slowdown, a prompt-tag break-out and README drift; the author probing their own code only finds what they can think of | Same-context probe found 2 real bugs in A1. **First fresh-agent attack (2026-10-23, about 110k subagent tokens, 5 minutes, no API spend): 13 findings, all reproduced; 5 fixed (2 high: unsanitised name, guardrails blind to links/phones/placeholders), 8 parked or sent to layer 4.** It found things our own probe missed: the name field, the guardrail blind spots, `NONE` variants, non-DE/EN pain, the latency worst case |
| 2026-10-22 | `.claude/skills/run-evals` | skill | Evals must be re-run after every prompt/model/router change; they cost money | New, not yet used |
| 2026-10-23 | `.claude/skills/retro` (manual only) + `devsetup/retro-log.md` | skill | We did a 14-item retrospective by hand and want to repeat it whenever a layer ends or something goes badly, including pruning the setup | New; first use is the next layer |
| 2026-10-23 | `/edge-case-probe` attack mode on A1 layer 4a (fresh agent, after our own reviews) | skill use | Layer 4a had been reviewed and tested; attack mode is the rule before calling a layer done | Found what our reviews had missed: (1) dedup key collision (email "x@y.de\nattacker" + message "Q" vs email "x@y.de" + message "attacker\nQ" gave the same key) that silently drops a patient; (2) retention crashed above about 65k due leads (Postgres 65535 bind-parameter limit); (3) personal data re-entering already anonymized leads and never cleaned; (4) NUL byte in `language` raising a DB error and losing the decision flags. 6 more findings parked (see the 4a REVIEW.md Parked list). Two earlier rulings were upgraded by the probe: the retention IN(ids) bind limit (parked as minor) and leaving `replies.error` unblanked. All four were fixed test-first (commit ecc3908) and passed a scoped re-review. |
| 2026-10-24 | `/edge-case-probe` attack mode, concurrency focus, on the A1 layer 4a repository (fresh opus agent, own `leads_probe` database) | skill use | Concurrency bugs only show under real parallel sessions, and our own tests had not run two sessions at once on most methods | Found a high bug we had introduced the same day: `set_classification` deadlocked with itself (a `FOR SHARE` check followed by an UPDATE of the same row; 52 of 60 rounds). Also: reads that left a broken or open transaction, stale cached rows after retention, a retention batch size that crashed every run. Fixed with regression tests and a rerun of the agent's pair matrix; the rest parked in the A1 REVIEW.md. Lesson: a new lock needs a test with two sessions racing, and a "plain read is not enough" fix needs the strength of the lock checked against what the method does next |
| 2026-10-22 | `devsetup/model-choice.md` | template | Choosing Haiku vs Sonnet needs evidence, not guesses | Haiku vs Sonnet drafter experiment recorded |
| 2026-10-22 | `.gitattributes` (`eol=lf`) | config | Windows line-ending warnings on every commit | New |
| 2026-10-22 | `.python-version` (3.12) | config | uv picked Python 3.15 and compiled Rust for minutes | Tests run in seconds |
| 2026-10-22 | `devsetup/safety-patterns.md` | checklist | A1's safety design should be reusable, but only where it applies | Type A verified in A1; types B and C are marked as hypotheses until Track C |
| 2026-10-22 | `devsetup/spec-template.md` | template | Every spec should state autonomy (workflow vs agent), safety type, models and evals up front | New; A1 spec got a retroactive section |
| 2026-10-23 | `precommit.py` skip visibility (`summarize`) | hook change | DB tests skip (not fail) when Postgres is down, so a green gate could hide 50 skipped tests | The gate now prints pytest's summary line and `N tests were SKIPPED (database not running?)`. Checked by hand: Postgres up gives 334 passed, Postgres stopped gives 284 passed, 50 skipped. Tested in `test_hooks.py` (trailing blanks, warnings, test names containing the word skipped) |
| 2026-10-23 | `test_hooks.py` helper scrubs `GIT_*` env vars | test fix | The gate runs the suite inside `git commit`; git exports `GIT_INDEX_FILE` etc., so the helper's temp-repo `git add` staged a fake `.env` and `notes.md` into the REAL index | Found during 4a: fake files ended up staged in the real repo and had to be noticed and unstaged. Fixed in the helper; the whole suite has run inside the gate since without a recurrence |

## What subagents inherit (checked 2026-10-23, Haiku test agent)
They get the project `CLAUDE.md`, can see our project skills (`edge-case-probe`, `run-evals`; not the manual-only `/retro`), have their own Agent tool, and the `guard.py` hook blocks their `.env` writes. Not tested: the Bash branch of the guard from inside a subagent (the agent declined to try). Where each skill runs is in `CLAUDE.md`, section "Using superpowers workflows with our skills".

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
