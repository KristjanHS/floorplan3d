"""Live test progress for scripts/gate_steps.sh; inert unless it sets GATE_PROGRESS_TTY.

The gate captures pytest's output (shown only on failure), so progress goes straight to the
terminal, pytest-style: each file's name as it starts, then a mark per test as it finishes.
A test still running after SLOW_S gets its own line with a live elapsed counter, so a long
test shows what is running instead of a silent wait. The summary feeds the gate's ok line.
"""

import os
import threading
import time
from typing import IO

import pytest

SLOW_S = 2.0
TICK_S = 0.5
CLEAR_EOL = "\x1b[K"

_tty: IO[str] | None = None
_lock = threading.Lock()  # the ticker thread and the hooks share the line state below
_stop = threading.Event()
_running: tuple[str, float] | None = None  # (nodeid, start) of the test in progress
_ticking = False  # the running test has its own live line
_file: str | None = None
_outcomes: dict[str, str] = {}
_counts: dict[str, int] = {}
_wrote = False
_midline = True  # the gate's "push-gate: test … " prefix is already on the line


def _write(text: str, *, ends_line: bool) -> None:
    global _wrote, _midline
    if _tty is not None:
        _tty.write(text)
        _tty.flush()
        _wrote, _midline = True, not ends_line


def _slow_line(nodeid: str, took: float, tail: str) -> str:
    return f"\r    {took:4.1f}s {nodeid.rsplit('::', 1)[-1]}{tail}{CLEAR_EOL}"


def _tick() -> None:
    global _ticking
    while not _stop.wait(TICK_S):
        with _lock:
            if _running is None:
                continue
            nodeid, start = _running
            took = time.monotonic() - start
            if took < SLOW_S:
                continue
            if not _ticking and _midline:
                _write("\n", ends_line=True)
            _ticking = True
            _write(_slow_line(nodeid, took, " …"), ends_line=False)


def pytest_configure(config: pytest.Config) -> None:
    global _tty
    path = os.environ.get("GATE_PROGRESS_TTY")
    if path and not hasattr(config, "workerinput"):
        try:
            _tty = open(path, "w")  # noqa: SIM115 — held for the session, closed in unconfigure
        except OSError:
            _tty = None
        else:
            threading.Thread(target=_tick, daemon=True).start()


def pytest_runtest_logstart(nodeid: str) -> None:
    global _running, _file
    with _lock:
        path = nodeid.split("::", 1)[0]
        if path != _file:
            _file = path
            _write(("\n" if _midline else "") + f"  {path} ", ends_line=False)
        _running = (nodeid, time.monotonic())


# pytest's own summary categories, in its order; a test's mark is its worst category.
_ORDER = ("failed", "passed", "skipped", "xfailed", "xpassed", "error")
_MARK = {"failed": "F", "error": "E", "xpassed": "X", "xfailed": "x", "skipped": "s", "passed": "."}
_WORST = ("failed", "error", "xpassed", "xfailed", "skipped", "passed")


def _category(report: pytest.TestReport) -> str | None:
    """Counted the way pytest's terminal summary counts: per report, not per test."""
    xfail = hasattr(report, "wasxfail")
    if report.failed:
        return "failed" if report.when == "call" else "error"
    if report.skipped:
        return "xfailed" if xfail else "skipped"
    if report.when == "call":
        return "xpassed" if xfail else "passed"
    return None


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    category = _category(report)
    if category is None:
        return
    _counts[category] = _counts.get(category, 0) + 1
    seen = _outcomes.get(report.nodeid)
    if seen is None or _WORST.index(category) < _WORST.index(seen):
        _outcomes[report.nodeid] = category


def pytest_runtest_logfinish(nodeid: str) -> None:
    global _running, _ticking
    with _lock:
        outcome = _outcomes.pop(nodeid, "passed")
        start = _running[1] if _running is not None and _running[0] == nodeid else time.monotonic()
        took = time.monotonic() - start
        _running = None
        if _ticking or took >= SLOW_S:
            lead = "\n" if _midline and not _ticking else ""
            mark = "" if outcome == "passed" else f" {outcome.upper()}"
            _write(lead + _slow_line(nodeid, took, mark) + "\n", ends_line=True)
            _ticking = False
        else:
            # After a slow test's own line, the file's remaining marks continue on an indented one.
            indent = "" if _midline else "    "
            _write(indent + _MARK[outcome], ends_line=False)


def pytest_unconfigure(config: pytest.Config) -> None:
    global _tty
    _stop.set()
    with _lock:
        if _tty is None:
            return
        if _wrote and _midline:
            _tty.write("\n")
        _tty.close()
        _tty = None
    state = os.environ.get("GATE_PROGRESS_STATE")
    if state and _wrote:
        label = {"error": "errors"}
        summary = ", ".join(f"{n} {k if n == 1 else label.get(k, k)}" for k in _ORDER if (n := _counts.get(k)))
        with open(state, "w") as f:
            f.write(summary)
