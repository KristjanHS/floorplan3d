"""Run a command under bwrap: no network, cleared env, read-only allowlisted FS plus the given writable dirs."""

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The venv's python links via uv's minor-version symlink (cpython-3.12-…) to the install prefix.
PYTHON_LINK = (ROOT / ".venv" / "bin" / "python").readlink().parents[1]
PYTHON_PREFIX = PYTHON_LINK.resolve()


def bwrap_cmd(
    cmd: list[str], rw: list[Path], chdir: Path, ro: Sequence[Path] = (), env: dict[str, str] | None = None
) -> list[str]:
    system = []
    for d in ("/bin", "/lib", "/lib64", "/sbin"):
        p = Path(d)
        system += ["--symlink", str(p.readlink()), d] if p.is_symlink() else ["--ro-bind-try", d, d]
    ro_binds = [arg for d in (ROOT, PYTHON_PREFIX, *ro) for arg in ("--ro-bind", str(d.resolve()), str(d.resolve()))]
    if PYTHON_LINK != PYTHON_PREFIX:
        ro_binds += ["--symlink", str(PYTHON_PREFIX), str(PYTHON_LINK)]
    # rw binds come after the ro repo bind so dirs under the repo stay writable.
    rw_binds = [arg for d in rw for arg in ("--bind", str(d.resolve()), str(d.resolve()))]
    base_env = {"PATH": "/usr/bin:/bin", "HOME": str(rw[0].resolve()), "LANG": "C.UTF-8"}
    setenv = [arg for k, v in {**base_env, **(env or {})}.items() for arg in ("--setenv", k, v)]
    return [
        "bwrap",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/etc", "/etc",
        *system,
        *ro_binds,
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        *rw_binds,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        *setenv,
        "--chdir", str(chdir.resolve()),
        "--", *cmd,
    ]  # fmt: skip


def run(cmd: list[str], rw: list[Path], chdir: Path, env: dict[str, str] | None = None) -> None:
    for d in rw:
        d.mkdir(parents=True, exist_ok=True)
    subprocess.run(bwrap_cmd(cmd, rw, chdir, env=env), check=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rw", type=Path, action="append", required=True, help="writable dir (repeatable; first = HOME)")
    p.add_argument("--env", action="append", default=[], help="KEY=VALUE set inside the sandbox (repeatable)")
    p.add_argument("--chdir", type=Path, default=Path.cwd())
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    a = p.parse_args()
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    try:
        run(cmd, a.rw, a.chdir, dict(kv.split("=", 1) for kv in a.env))
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
