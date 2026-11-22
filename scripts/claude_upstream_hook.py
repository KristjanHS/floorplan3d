"""Claude Code PreToolUse(Bash) hook: deny Python that runs upstream FloorplanToBlender3d code outside the sandbox.

Upstream runs only under scripts/sandbox.py (bwrap: no network, cleared env, repo read-only). The command is split
into segments (; && || | & newline, heredoc bodies kept); `cd` moves the cwd. Only Python runners are checked
(python*, uv run x.py, ./x.py; pytest is allowed, tests/conftest.py's .pyc tripwire covers it): inline code or
stdin naming upstream is denied, as is a script file naming it unless it imports sandbox or IS scripts/sandbox.py.
Stdlib only, run as `python3 -I`; fails open, since scripts/upstream_guard.py and the tripwire still stand behind it.
"""

import contextlib
import json
import re
import shlex
import sys
from pathlib import Path

UPSTREAM = re.compile(r"FloorplanToBlender|ftb_generate|ftb_probe")
SANDBOXED_FILE = re.compile(r"^\s*(import sandbox\b|from sandbox import)", re.MULTILINE)
SANDBOX = Path(__file__).resolve().parent / "sandbox.py"
HEREDOC = re.compile(r"<<(?P<dash>-?)\s*(?P<q>['\"]?)(?P<delim>[\w.-]+)(?P=q)")
ASSIGN = re.compile(r"[A-Za-z_]\w*=")
PYTHON = re.compile(r"i?python[0-9.]*")
UV_VALUE_FLAGS = {"--project", "--directory", "--with", "--with-requirements", "--python", "-p", "--group", "-w"}
UV_VALUE_FLAGS |= {"--extra", "--package", "--env-file", "--index", "--only-group", "--no-group"}
MAX_READ = 1_000_000

REASON = (
    "Blocked: this runs upstream FloorplanToBlender3d code outside scripts/sandbox.py ({where}). Wrap it: "
    "uv run python scripts/sandbox.py --rw <scratch dir> --env MPLBACKEND=Agg -- .venv/bin/python -I <script>. "
    "If no upstream code actually runs, ask the user to run the command."
)


def _heredoc_bodies(command: str, i: int, pending: list[tuple[str, bool]], body: list[str]) -> int:
    """Append the bodies of the pending heredocs, which start at index i; return the index after them."""
    for delim, dash in pending:
        while i < len(command):
            j = command.find("\n", i)
            line, i = (command[i:], len(command)) if j < 0 else (command[i:j], j + 1)
            if (line.lstrip("\t") if dash else line) == delim:
                break
            body.append(line)
    return i


def segments(command: str) -> list[tuple[str, str]]:
    """(text, heredoc body) per simple command, split on unquoted ; & | ( ) and newlines."""
    out: list[tuple[str, str]] = []
    cur: list[str] = []
    body: list[str] = []
    pending: list[tuple[str, bool]] = []
    quote, i = None, 0
    while i < len(command):
        c, nxt = command[i], command[i + 1 : i + 2]
        if quote:
            if c == "\\" and quote == '"' and nxt:
                cur.append(c + nxt)
                i += 2
                continue
            quote = None if c == quote else quote
        elif c == "\\" and nxt:
            cur.append(" " if nxt == "\n" else c + nxt)
            i += 2
            continue
        elif c in "'\"":
            quote = c
        elif command.startswith("<<<", i):
            cur.append("<<<")
            i += 3
            continue
        elif (m := HEREDOC.match(command, i)) is not None:
            pending.append((m.group("delim"), bool(m.group("dash"))))
            cur.append(m.group(0))
            i = m.end()
            continue
        elif c in ";&|()\n" and not (c == "&" and ((cur and cur[-1][-1:] in "<>") or nxt == ">")):
            i += 1
            if c == "\n" and pending:
                i = _heredoc_bodies(command, i, pending, body)
                pending = []
            if "".join(cur).strip() or body:
                out.append(("".join(cur), "\n".join(body)))
            cur, body = [], []
            continue
        cur.append(c)
        i += 1
    if "".join(cur).strip() or body:
        out.append(("".join(cur), "\n".join(body)))
    return out


def _words(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _strip_launchers(words: list[str]) -> list[str]:
    """Drop VAR=x assignments, `env [opts]` and `uv run [flags]` in front of the real program."""
    while words:
        if ASSIGN.match(words[0]):
            words = words[1:]
        elif words[0] == "env":
            words = words[1:]
            while words and (words[0].startswith("-") or ASSIGN.match(words[0])):
                words = words[1:]
        elif words[:2] == ["uv", "run"]:
            words = words[2:]
            while words and words[0].startswith("-"):
                flag = words.pop(0)
                if flag == "--":
                    break
                if flag in UV_VALUE_FLAGS and words:
                    words.pop(0)
        else:
            break
    return words


def runner(words: list[str]) -> tuple[str, str] | None:
    """What a Python runner executes: ("pytest"|"file"|"code"|"module"|"stdin", arg), or None for no runner."""
    words = _strip_launchers(words)
    if not words:
        return None
    prog = Path(words[0]).name
    if prog in ("pytest", "py.test"):
        return ("pytest", "")
    if prog.endswith(".py"):
        return ("file", words[0])
    if not PYTHON.fullmatch(prog):
        return None
    args = words[1:]
    while args:
        a = args.pop(0)
        if a == "-":
            return ("stdin", "")
        if a == "--":
            return ("file", args[0]) if args else ("stdin", "")
        if a.startswith("--"):
            continue
        if not a.startswith("-"):
            return ("file", a)
        for k, ch in enumerate(a[1:], 2):
            if ch in "cmWX":
                val = a[k:] or (args.pop(0) if args else "")
                if ch == "c":
                    return ("code", val)
                if ch == "m":
                    return ("pytest", "") if val in ("pytest", "py.test") else ("module", val)
                break
    return ("stdin", "")


def _file_runs_upstream(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size > MAX_READ or path.resolve() == SANDBOX:
        return False
    text = path.read_text(errors="replace")
    return bool(UPSTREAM.search(text)) and not SANDBOXED_FILE.search(text)


def verdict(command: str, cwd: Path) -> str | None:
    """Why the command is denied, or None to allow it."""
    for text, body in segments(command):
        words = _words(text)
        if words[:1] == ["cd"]:
            target = words[1] if len(words) > 1 else "~"
            if target != "-":
                cwd = cwd / Path(target).expanduser()
            continue
        run = runner(words)
        if run is None or run[0] == "pytest":
            continue
        kind, arg = run
        if kind == "file":
            if _file_runs_upstream(cwd / Path(arg).expanduser()):
                return shlex.quote(arg)
        elif UPSTREAM.search(text + "\n" + body):
            return "command text"
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
