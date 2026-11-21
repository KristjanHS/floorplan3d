"""Run a command under bwrap: no network, read-only filesystem except the given writable dirs."""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def bwrap_cmd(cmd: list[str], rw: list[Path], chdir: Path) -> list[str]:
    binds = [arg for d in rw for arg in ("--bind", str(d.resolve()), str(d.resolve()))]
    return [
        "bwrap",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        *binds,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--chdir", str(chdir.resolve()),
        "--", *cmd,
    ]  # fmt: skip


def run(cmd: list[str], rw: list[Path], chdir: Path, env: dict[str, str] | None = None) -> None:
    for d in rw:
        d.mkdir(parents=True, exist_ok=True)
    subprocess.run(bwrap_cmd(cmd, rw, chdir), check=True, env={**os.environ, **(env or {})})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rw", type=Path, action="append", default=[], help="writable dir (repeatable)")
    p.add_argument("--chdir", type=Path, default=Path.cwd())
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    a = p.parse_args()
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    try:
        run(cmd, a.rw, a.chdir)
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
