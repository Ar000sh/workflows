"""Tests for the guard and pre-commit hooks. Run with the normal suite (`uv run pytest`)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent
FAKE_KEY = "sk-ant-" + "x" * 20     # built at runtime so no key-shaped literal sits in this file


def guard(tool_name: str, **tool_input) -> int:
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    return subprocess.run([sys.executable, str(HOOKS / "guard.py")], input=payload, text=True,
                          capture_output=True).returncode


@pytest.mark.parametrize("tool, data", [
    ("Write", {"file_path": "C:/x/.env", "content": "A=1"}),
    ("Edit", {"file_path": "x/.env.local", "new_string": "A=1"}),
    ("Write", {"file_path": "a.md", "content": f"key {FAKE_KEY}"}),
    ("Edit", {"file_path": "a.py", "new_string": f"KEY = '{FAKE_KEY}'"}),
    ("Bash", {"command": f"echo {FAKE_KEY}"}),
    ("Bash", {"command": "echo A=1 > .env"}),
    ("Bash", {"command": "echo A=1 >> .env"}),
    ("Bash", {"command": "cp .env.example .env"}),
    ("Bash", {"command": "echo A=1 | tee .env"}),
])
def test_guard_blocks(tool, data):
    assert guard(tool, **data) == 2


@pytest.mark.parametrize("tool, data", [
    ("Write", {"file_path": "C:/x/.env.example", "content": "ANTHROPIC_API_KEY="}),
    ("Edit", {"file_path": "x/a.py", "new_string": "print(1)"}),
    ("Write", {"file_path": "a.md", "content": "keys start with sk-ant- and live in .env"}),
    ("Bash", {"command": "cat .env"}),
    ("Bash", {"command": "git add .env.example"}),
    ("Bash", {"command": "ls .venv > out.txt"}),
    ("Read", {"file_path": "x/.env"}),
])
def test_guard_allows(tool, data):
    assert guard(tool, **data) == 0


def test_guard_never_blocks_on_garbage_input():
    result = subprocess.run([sys.executable, str(HOOKS / "guard.py")], input="not json", text=True,
                            capture_output=True)
    assert result.returncode == 0


def _repo_with(tmp_path: Path, filename: str, content: str) -> subprocess.CompletedProcess:
    # When this suite runs INSIDE a real `git commit` (the pre-commit gate), git exports GIT_INDEX_FILE,
    # GIT_DIR etc. Without scrubbing them, the "temporary" repo's `git add` would stage files into the
    # REAL index (a fake .env and notes.md ended up staged in the real repo).
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    run = lambda *args: subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True, env=env)
    run("init", "-q")
    (tmp_path / filename).write_text(content, encoding="utf-8")
    run("add", "-f", filename)
    return subprocess.run([sys.executable, str(HOOKS / "precommit.py")], cwd=tmp_path, capture_output=True, text=True,
                          env=env)


sys.path.insert(0, str(HOOKS))
from precommit import report, summarize  # noqa: E402


@pytest.mark.parametrize("output, expected", [
    ("278 passed in 9s", ("278 passed in 9s", 0)),
    ("270 passed, 8 skipped in 9s", ("270 passed, 8 skipped in 9s", 8)),
    # trailing blank lines and a warnings block before the summary must not confuse it
    ("..s\nwarnings here\n\n1 passed, 1 skipped, 2 warnings in 0.50s\n\n\n", ("1 passed, 1 skipped, 2 warnings in 0.50s", 1)),
    # decorated (non -q) form and a failure line
    ("===== 3 failed, 5 passed, 2 skipped in 1.2s =====\n", ("3 failed, 5 passed, 2 skipped in 1.2s", 2)),
    # an earlier test name containing "skipped in 3s" must not win over the real last line
    ("test_x skipped in 3s\n4 passed in 1s\n", ("4 passed in 1s", 0)),
])
def test_precommit_summary_line_is_extracted(output, expected):
    assert summarize(output) == expected


# The gate's printed lines: a pass line, plus an exact warning when tests were skipped.
def test_precommit_report_warns_about_skipped_tests():
    assert report("1 passed, 2 skipped in 1s") == [
        "pre-commit: tests passed (1 passed, 2 skipped in 1s)",
        "pre-commit: 2 tests were SKIPPED (database not running?)",
    ]


def test_precommit_report_has_no_warning_without_skips():
    assert report("3 passed in 1s") == ["pre-commit: tests passed (3 passed in 1s)"]
    assert report("boom") == ["pre-commit: tests passed"]


# No summary line (pytest crashed, or output is empty): empty text and 0, so the caller prints a plain message.
def test_precommit_summary_without_summary_line():
    assert summarize("Traceback...\nboom\n") == ("", 0)
    assert summarize("") == ("", 0)


def test_precommit_blocks_staged_env_file(tmp_path):
    result = _repo_with(tmp_path, ".env", "A=1\n")
    assert result.returncode == 1 and "secrets file staged" in result.stderr


def test_precommit_blocks_staged_key(tmp_path):
    result = _repo_with(tmp_path, "notes.md", f"my key: {FAKE_KEY}\n")
    assert result.returncode == 1 and "API key" in result.stderr


def test_precommit_allows_env_example_and_skips_tests_for_docs_only(tmp_path):
    result = _repo_with(tmp_path, "notes.md", "just docs\n")
    assert result.returncode == 0 and "tests skipped" in result.stdout
