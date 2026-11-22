import json
import subprocess
from pathlib import Path

import pytest
from conftest import stray_vendor_pyc

import claude_upstream_hook as hook
import plans
import sandbox

pytestmark = pytest.mark.ftb

PYTHON = str(plans.ROOT / ".venv" / "bin" / "python")
SCRIPTS = plans.ROOT / "scripts"


@pytest.mark.parametrize("script", ["ftb_generate.py", "ftb_probe.py"])
def test_upstream_script_refuses_a_writable_repo(script: str, tmp_path: Path):
    # bwrap (no network, no home) with the repo bound rw: a guard regression runs upstream here, not on the host.
    cmd = sandbox.bwrap_cmd([PYTHON, "-I", str(SCRIPTS / script), "x.ini"], [tmp_path, plans.ROOT], tmp_path)
    before = stray_vendor_pyc()
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode != 0
    assert "only via scripts/sandbox.py" in res.stderr
    assert stray_vendor_pyc() == before  # exited before any upstream import


SCRATCH_PROBE = """import sys
sys.path.insert(0, "/repo/vendor/FloorplanToBlender3d")
from FloorplanToBlenderLib import detect
"""

DENY = {
    "inline heredoc probe": f".venv/bin/python - <<'EOF'\n{SCRATCH_PROBE}EOF",
    "python -c import": "uv run python -c 'from FloorplanToBlenderLib import detect'",
    "glue script direct": ".venv/bin/python -I scripts/ftb_generate.py work/x/floor.ini",
    "scratch file": ".venv/bin/python {probe}",
    "uv run scratch file": "uv run {probe}",
    "sandbox named in another segment": "cat scripts/sandbox.py; .venv/bin/python -c 'import FloorplanToBlenderLib'",
    "cd then relative probe": "cd {probe.parent} && .venv/bin/python probe.py",
    "comma import": ".venv/bin/python {comma_probe}",
    "probe under a tests dir": ".venv/bin/python {tests_probe}",
}
# Each names python/pytest, so the runner logic is exercised rather than skipped outright.
ALLOW = {
    "sandboxed glue": "uv run python scripts/sandbox.py --rw /tmp/s -- .venv/bin/python -I scripts/ftb_probe.py x.ini",
    "lint then pytest": "uv run ruff check scripts/ftb_probe.py && uv run pytest -q",
    "pyright then pytest": "uv run pyright scripts/ftb_generate.py && uv run pytest -q",
    "grep for python": "grep -n python scripts/ftb_generate.py",
    "commit message": 'git commit -m "fix ftb_probe: run pytest"',
    "git log then pytest": "git log --grep=ftb_probe && uv run pytest -q",
    "one test file": "uv run pytest -q tests/test_ftb_probe.py",
    "python, no upstream": "uv run python scripts/measure.py",
    "lint the hook itself": "uv run ruff check scripts/claude_upstream_hook.py && uv run pytest -q",
}


@pytest.fixture
def probes(tmp_path: Path) -> dict[str, Path]:
    (tmp_path / "tests").mkdir()
    files = {
        "probe": (tmp_path / "probe.py", SCRATCH_PROBE),
        "comma_probe": (tmp_path / "comma.py", "import sys, FloorplanToBlenderLib\n"),
        "tests_probe": (tmp_path / "tests" / "scratch_probe.py", SCRATCH_PROBE),
    }
    for path, text in files.values():
        path.write_text(text)
    return {key: path for key, (path, _text) in files.items()}


@pytest.mark.parametrize("name", DENY)
def test_hook_denies_upstream_python_outside_the_sandbox(name: str, probes: dict[str, Path]):
    assert hook.verdict(DENY[name].format(**probes), plans.ROOT) is not None


@pytest.mark.parametrize("name", ALLOW)
def test_hook_allows(name: str):
    assert hook.verdict(ALLOW[name], plans.ROOT) is None


def test_hook_emits_a_deny_decision():
    event = {"tool_name": "Bash", "cwd": str(plans.ROOT), "tool_input": {"command": DENY["glue script direct"]}}
    res = subprocess.run(
        ["python3", "-I", str(SCRIPTS / "claude_upstream_hook.py")],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        check=True,
    )
    out = json.loads(res.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert "scripts/sandbox.py" in out["permissionDecisionReason"]


def test_settings_wires_the_hook():
    settings = json.loads((plans.ROOT / ".claude" / "settings.json").read_text())
    [entry] = [e for e in settings["hooks"]["PreToolUse"] if e["matcher"] == "Bash"]
    assert any("scripts/claude_upstream_hook.py" in h["command"] for h in entry["hooks"])
