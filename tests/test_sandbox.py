import json
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

import plans
import sandbox

SOCKETS = ("/run/user/1000/bus", "/var/run/docker.sock", "/run/dbus/system_bus_socket")
PROBE = """
import json, os, socket, sys
def ok(f):
    try:
        f()
        return True
    except OSError:
        return False
def unix(p):
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(p)
def write(p):
    with open(p, "w") as f:
        f.write("x")
print(json.dumps({
    "unix": {p: ok(lambda p=p: unix(p)) for p in sys.argv[3:]},
    "tcp": ok(lambda: socket.create_connection(("1.1.1.1", 53), timeout=3).close()),
    "write_repo": ok(lambda: write(sys.argv[1])),
    "write_rw": ok(lambda: write(sys.argv[2])),
    "secret": os.environ.get("FLOORPLAN_SECRET_PROBE"),
}))
"""

pytestmark = [pytest.mark.ftb, pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not on PATH")]


def host_connectable(path: str) -> bool:
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(path)
        return True
    except OSError:
        return False


def test_sandbox_blocks_sockets_network_writes_and_env():
    live = [p for p in SOCKETS if host_connectable(p)]
    assert live, f"no connectable host socket among {SOCKETS}: probe would be vacuous"
    plans.WORK.mkdir(exist_ok=True)
    rw = Path(tempfile.mkdtemp(prefix="pytest-sandbox-", dir=plans.WORK))  # sandbox hides host /tmp
    repo_file = plans.ROOT / "sandbox-probe.tmp"
    try:
        python = str(plans.ROOT / ".venv" / "bin" / "python")
        cmd = sandbox.bwrap_cmd([python, "-I", "-c", PROBE, str(repo_file), str(rw / "ok"), *live], [rw], rw)
        env = {**os.environ, "FLOORPLAN_SECRET_PROBE": "1"}
        out = subprocess.run(cmd, check=True, capture_output=True, text=True, env=env)
        r = json.loads(out.stdout)
        assert r["unix"] == dict.fromkeys(live, False)
        assert r["secret"] is None
        assert not r["tcp"]
        assert not r["write_repo"] and not repo_file.exists()
        assert r["write_rw"] and (rw / "ok").read_text() == "x"
    finally:
        shutil.rmtree(rw)
        repo_file.unlink(missing_ok=True)
