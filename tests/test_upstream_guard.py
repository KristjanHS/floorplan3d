import json
import subprocess
from pathlib import Path

import pytest
from conftest import stray_vendor_pyc

import claude_upstream_hook as hook
import plans

PYTHON = str(plans.ROOT / ".venv" / "bin" / "python")
SCRIPTS = plans.ROOT / "scripts"


@pytest.mark.parametrize("script", ["ftb_generate.py", "ftb_probe.py"])
def test_upstream_script_refuses_to_run_on_the_host(script: str):
    before = stray_vendor_pyc()
    res = subprocess.run([PYTHON, "-I", str(SCRIPTS / script), "x.ini"], capture_output=True, text=True, check=False)
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
}
ALLOW = {
    "sandboxed glue": "uv run python scripts/sandbox.py --rw /tmp/s -- .venv/bin/python -I scripts/ftb_probe.py x.ini",
    "make target": "make probe FLOOR=x",
    "whole suite": "uv run pytest -q",
    "one test file": "uv run pytest -q tests/test_ftb_probe.py",
    "static check": "uv run pyright scripts/ftb_probe.py",
    "grep upstream": "grep -rn FloorplanToBlenderLib scripts",
    "python, no upstream": "uv run python scripts/measure.py",
    "lint the hook itself": "uv run ruff check scripts/claude_upstream_hook.py && uv run pytest -q",
}


@pytest.fixture
def probe(tmp_path: Path) -> Path:
    p = tmp_path / "probe.py"
    p.write_text(SCRATCH_PROBE)
    return p


@pytest.mark.parametrize("name", DENY)
def test_hook_denies_upstream_python_outside_the_sandbox(name: str, probe: Path):
    assert hook.verdict(DENY[name].format(probe=probe), plans.ROOT) is not None


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
    [entry] = settings["hooks"]["PreToolUse"]
    assert entry["matcher"] == "Bash"
    assert "scripts/claude_upstream_hook.py" in entry["hooks"][0]["command"]
