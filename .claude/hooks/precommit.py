"""Pre-commit checks, run by .githooks/pre-commit for every `git commit` (Tariq's and Claude's).

1. No .env file staged (.env.example is fine).
2. No Anthropic API key literal in the added lines.
3. The test suite passes. Skipped when only .md files are staged. Limited to TIMEOUT seconds.

If the tests run into the timeout, do NOT bypass the check on your own: ask Tariq whether the rule
should change (for example, run only the relevant tests). Never use --no-verify.
"""
import os
import re
import subprocess
import sys

KEY_IN_ADDED_LINES = re.compile(r"^\+(?!\+\+).*sk-ant-[A-Za-z0-9_-]{16,}", re.MULTILINE)
ENV_FILE = re.compile(r"(^|/)\.env(\.(?!example$)[^/]+)?$")
TIMEOUT = int(os.environ.get("PRECOMMIT_TEST_TIMEOUT", "90"))


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.stdout


def fail(message: str) -> None:
    print(f"PRE-COMMIT BLOCKED: {message}", file=sys.stderr)
    sys.exit(1)


# pytest's last line looks like "270 passed, 8 skipped in 9.1s" (with -q) or the same wrapped in "=====".
SUMMARY_LINE = re.compile(r"^=*\s*(\d+ [a-z]+(?:, \d+ [a-z]+)* in [\d.]+s(?: \([\d:]+\))?)\s*=*$")
SKIPPED = re.compile(r"(\d+) skipped")


# Finds pytest's final summary line and how many tests were skipped. We search from the END
# (trailing blank lines or warnings do not matter) and require "N word... in Xs", so a test name
# that merely contains "skipped" cannot match. Returns ("", 0) if there is no such line.
# Why it matters: DB tests skip (not fail) when Postgres is down, and a green gate must not hide that.
def summarize(output: str) -> tuple[str, int]:
    for line in reversed(output.splitlines()):
        match = SUMMARY_LINE.match(line.strip())
        if match:
            summary = match.group(1)
            skipped = SKIPPED.search(summary)
            return summary, int(skipped.group(1)) if skipped else 0
    return "", 0


def kill_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
    else:
        process.kill()


def main() -> None:
    staged = [line for line in git("diff", "--cached", "--name-only").splitlines() if line.strip()]
    if not staged:
        return

    secrets_files = [path for path in staged if ENV_FILE.search(path)]
    if secrets_files:
        fail(f"secrets file staged: {', '.join(secrets_files)}. Run: git restore --staged <file>")

    if KEY_IN_ADDED_LINES.search(git("diff", "--cached", "-U0")):
        fail("an Anthropic API key (sk-ant-...) is in the staged changes. Remove it and rotate the key if it was ever pushed.")

    if all(path.endswith(".md") for path in staged):
        print("pre-commit: docs-only commit, tests skipped")
        return

    print(f"pre-commit: running tests (limit {TIMEOUT}s)...")
    process = subprocess.Popen(
        ["uv", "run", "pytest", "-q", "-x"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    try:
        output, _ = process.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        fail(
            f"the tests did not finish within {TIMEOUT}s. Do not bypass this check: ask Tariq whether to change "
            "the rule (for example, run only the relevant tests, or raise PRECOMMIT_TEST_TIMEOUT)."
        )
    if process.returncode != 0:
        print("\n".join(output.splitlines()[-30:]), file=sys.stderr)
        fail("tests failed. Fix them before committing.")
    summary, skipped = summarize(output)
    print(f"pre-commit: tests passed ({summary})" if summary else "pre-commit: tests passed")
    if skipped:
        print(f"pre-commit: {skipped} tests were SKIPPED (database not running?)")


if __name__ == "__main__":
    main()
