"""Live test progress for scripts/gate_steps.sh; inert unless it sets GATE_PROGRESS_TTY.

The gate captures pytest's output (shown only on failure), so progress goes straight to the
terminal: a dot per fast test, a timed line per slow one, and a summary for the gate's ok line.
"""

import os
import time
from typing import IO

import pytest

SLOW_S = 2.0

_tty: IO[str] | None = None
_started: dict[str, float] = {}
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


def pytest_configure(config: pytest.Config) -> None:
    global _tty
    path = os.environ.get("GATE_PROGRESS_TTY")
    if path and not hasattr(config, "workerinput"):
        try:
            _tty = open(path, "w")  # noqa: SIM115 — held for the session, closed in unconfigure
        except OSError:
            _tty = None


def pytest_runtest_logstart(nodeid: str) -> None:
    _started[nodeid] = time.monotonic()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.failed:
        _outcomes[report.nodeid] = "failed"
    elif report.skipped:
        _outcomes.setdefault(report.nodeid, "skipped")
    elif report.when == "call":
        _outcomes.setdefault(report.nodeid, "passed")


def pytest_runtest_logfinish(nodeid: str) -> None:
    outcome = _outcomes.get(nodeid, "passed")
    _counts[outcome] = _counts.get(outcome, 0) + 1
    took = time.monotonic() - _started.pop(nodeid, time.monotonic())
    if took >= SLOW_S:
        mark = "" if outcome == "passed" else f" {outcome.upper()}"
        lead = "\n" if _midline else ""
        _write(f"{lead}    {took:4.1f}s {nodeid.rsplit('::', 1)[-1]}{mark}\n", ends_line=True)
    else:
        indent = "" if _midline else "  "
        _write(indent + {"passed": ".", "failed": "F", "skipped": "s"}[outcome], ends_line=False)


def pytest_unconfigure(config: pytest.Config) -> None:
    global _tty
    if _tty is None:
        return
    if _wrote and _midline:
        _tty.write("\n")
    _tty.close()
    _tty = None
    state = os.environ.get("GATE_PROGRESS_STATE")
    if state and _wrote:
        order = ("failed", "passed", "skipped")
        summary = ", ".join(f"{_counts[k]} {k}" for k in order if _counts.get(k))
        with open(state, "w") as f:
            f.write(summary)
