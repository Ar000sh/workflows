"""PreToolUse hook: keep secrets out of files and commands.

Blocks (exit code 2; the message goes back to Claude):
- Write/Edit to a .env file. Tariq edits secrets himself; .env.example is allowed.
- Any Write/Edit/Bash input containing an Anthropic API key literal
  (the prefix "sk-ant-" followed by 16 or more key characters).
- A Bash command that writes to a .env file (redirect, tee, cp, mv).

Any problem inside this hook itself lets the call through (exit 0): a broken hook must not freeze work.
The same key scan runs again on staged files in precommit.py.
"""
import json
import re
import sys

KEY = re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}")
# .env and .env.local etc., but not .env.example
ENV_FILE = re.compile(r"(^|[\\/])\.env(\.(?!example$)[^\\/]+)?$")
# redirect / tee / cp / mv whose target is a .env file
BASH_ENV_WRITE = re.compile(
    r"(>>?\s*|\btee\s+(-a\s+)?|\b(cp|mv)\s+[^;&|\n]*\s)[^\s;&|]*\.env(?!\.example)(?![A-Za-z0-9_])"
)


def block(message: str) -> None:
    print(f"BLOCKED by .claude/hooks/guard.py: {message}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    tool = payload.get("tool_name", "")
    data = payload.get("tool_input") or {}

    if tool in ("Write", "Edit", "MultiEdit"):
        path = str(data.get("file_path", ""))
        if ENV_FILE.search(path):
            block(f"{path} is a secrets file. Tariq edits .env himself; put variable names in .env.example only.")
        text = " ".join(str(data.get(k, "")) for k in ("content", "new_string"))
        for edit in data.get("edits") or []:
            text += " " + str(edit.get("new_string", ""))
        if KEY.search(text):
            block("the text contains an Anthropic API key (sk-ant-...). Never write keys into files.")

    if tool == "Bash":
        command = str(data.get("command", ""))
        if KEY.search(command):
            block("the command contains an Anthropic API key (sk-ant-...). Keys live only in .env, typed by Tariq.")
        if BASH_ENV_WRITE.search(command):
            block("the command writes to a .env file. Tariq edits .env himself.")


if __name__ == "__main__":
    main()
