"""Live test progress for scripts/gate_steps.sh; inert unless it sets GATE_PROGRESS_TTY.

The gate captures pytest's output (shown only on failure), so progress goes straight to the
terminal, pytest-style: each file's name as it starts, then pytest's own letters per test as it
finishes. A test still running after SLOW_S gets its own line with a live elapsed counter and,
once done, pytest's verbose words. pytest's summary stats line feeds the gate's ok line.
"""

import os
import threading
import time
from typing import IO

import pytest

SLOW_S = 2.0
TICK_S = 0.5
CLEAR_EOL = "\x1b[K"

_config: pytest.Config | None = None
_tty: IO[str] | None = None
_lock = threading.Lock()  # the ticker thread and the hooks share the line state below
_stop = threading.Event()
_running: tuple[str, float] | None = None  # (nodeid, start) of the test in progress
_ticking = False  # the running test has its own live line
_file: str | None = None
_status: dict[str, list[pytest.TestShortLogReport]] = {}  # a test's reports, flushed at its finish
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
    global _config, _tty
    path = os.environ.get("GATE_PROGRESS_TTY")
    if path and not hasattr(config, "workerinput"):
        try:
            _tty = open(path, "w")  # noqa: SIM115 — held for the session, closed in unconfigure
        except OSError:
            _tty = None
        else:
            _config = config
            threading.Thread(target=_tick, daemon=True).start()


def pytest_runtest_logstart(nodeid: str) -> None:
    global _running, _file
    with _lock:
        path = nodeid.split("::", 1)[0]
        if path != _file:
            _file = path
            _write(("\n" if _midline else "") + f"  {path} ", ends_line=False)
        _running = (nodeid, time.monotonic())


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if _config is not None:
        status = _config.hook.pytest_report_teststatus(report=report, config=_config)
        _status.setdefault(report.nodeid, []).append(pytest.TestShortLogReport(*status))


def pytest_runtest_logfinish(nodeid: str) -> None:
    global _running, _ticking
    with _lock:
        statuses = _status.pop(nodeid, [])
        start = _running[1] if _running is not None and _running[0] == nodeid else time.monotonic()
        took = time.monotonic() - start
        _running = None
        if _ticking or took >= SLOW_S:
            lead = "\n" if _midline and not _ticking else ""
            words = [s.word[0] if isinstance(s.word, tuple) else s.word for s in statuses]
            tail = "".join(f" {w}" for w in words if w)
            _write(lead + _slow_line(nodeid, took, tail) + "\n", ends_line=True)
            _ticking = False
        else:
            # After a slow test's own line, the file's remaining letters continue on an indented one.
            indent = "" if _midline else "    "
            _write(indent + "".join(s.letter for s in statuses), ends_line=False)


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    state = os.environ.get("GATE_PROGRESS_STATE")
    if _tty is not None and _wrote and state:
        parts, _color = terminalreporter.build_summary_stats_line()
        with open(state, "w") as f:
            f.write(", ".join(text for text, _markup in parts))


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
