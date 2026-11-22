"""Claude Code PreToolUse(Bash) hook: deny Python that runs upstream FloorplanToBlender3d code outside the sandbox.

Upstream runs only under scripts/sandbox.py (bwrap: no network, cleared env, repo read-only). Text match, by
design: a command that starts Python (python*, pytest) is denied when its own text names upstream, or a .py file
it names imports upstream (or launches the glue scripts), unless the command or that file goes through the
sandbox. Files under tests/ are skipped: tests/conftest.py's .pyc tripwire covers them. Stdlib only, run as
`python3 -I`; fails open, since scripts/upstream_guard.py and the tripwire still stand behind it.
"""

import contextlib
import json
import re
import shlex
import sys
from pathlib import Path

RUNS_PYTHON = re.compile(r"(?<![\w.-])(i?python[0-9.]*|pytest)(?![\w-])")
CMD_UPSTREAM = re.compile(r"FloorplanToBlender|ftb_generate|ftb_probe")
FILE_UPSTREAM = re.compile(
    r"^\s*(from|import)\s+(FloorplanToBlenderLib|ftb_generate|ftb_probe)\b|ftb_(generate|probe)\.py", re.MULTILINE
)
SANDBOXED_CMD = re.compile(r"(?<![\w-])(sandbox\.py|bwrap)(?![\w-])")
SANDBOXED_FILE = re.compile(r"^\s*(import sandbox\b|from sandbox import)", re.MULTILINE)
PY_TOKEN = re.compile(r"""[^\s'"`;|&()<>]+\.py\b(::\S*)?""")
MAX_READ = 1_000_000

REASON = (
    "Blocked: this runs upstream FloorplanToBlender3d code outside scripts/sandbox.py ({where}). Wrap it: "
    "uv run python scripts/sandbox.py --rw <scratch dir> --env MPLBACKEND=Agg -- .venv/bin/python -I <script>. "
    "If no upstream code actually runs, ask the user to run the command."
)


def _file_runs_upstream(token: str, cwd: Path) -> bool:
    path = Path(token.split("::", 1)[0])
    path = path if path.is_absolute() else cwd / path
    if "tests" in path.parts or not path.is_file() or path.stat().st_size > MAX_READ:
        return False
    text = path.read_text(errors="replace")
    return bool(FILE_UPSTREAM.search(text)) and not SANDBOXED_FILE.search(text)


def verdict(command: str, cwd: Path) -> str | None:
    """Why the command is denied, or None to allow it."""
    if not RUNS_PYTHON.search(command) or SANDBOXED_CMD.search(command):
        return None
    if CMD_UPSTREAM.search(PY_TOKEN.sub(" ", command)):
        return "command text"
    for m in PY_TOKEN.finditer(command):
        if _file_runs_upstream(m.group(0), cwd):
            return shlex.quote(m.group(0))
    return None


def main() -> None:
    event = json.load(sys.stdin)
    command = event.get("tool_input", {}).get("command", "")
    where = verdict(command, Path(event.get("cwd") or "."))
    if where is not None:
        decision = {"hookEventName": "PreToolUse", "permissionDecision": "deny"}
        decision["permissionDecisionReason"] = REASON.format(where=where)
        json.dump({"hookSpecificOutput": decision}, sys.stdout)


if __name__ == "__main__":
    with contextlib.suppress(Exception):  # a broken hook must not block every Bash call
        main()
    sys.exit(0)
