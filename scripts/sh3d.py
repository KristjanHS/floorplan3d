"""Sweet Home 3D `.sh3d` (a ZIP) <-> an unpacked, git-tracked source tree. Format facts: NOTES.md § SweetHome3D format."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
HASH = "sha256"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SH3D_VERSION_PINNED = "7.6.1"
DROP_HOME_ENTRY = False  # NOTES.md (a): Home.xml wins over Home in 7.6.1
DROPPED = frozenset({"ContentDigests", "Home"} if DROP_HOME_ENTRY else {"ContentDigests"})
META = ".sh3d_meta.json"


class Sh3dError(Exception):
    pass


@dataclass(frozen=True)
class Roots:
    homes: Path
    src: Path
    backups: Path
    work: Path
    dtd: Path

    def sh3d(self, name: str) -> Path:
        return self.homes / f"{name}.sh3d"

    def tree(self, name: str) -> Path:
        return self.src / name


def sha256_file(path: Path) -> str:
    digest = hashlib.new(HASH)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise Sh3dError(f"bad name {name!r}: use letters, digits, '_' or '-'")


def check_entry_name(entry: str) -> None:
    path = PurePosixPath(entry)
    if path.is_absolute() or "\\" in entry or ".." in path.parts or entry.rstrip("/") == META:
        raise Sh3dError(f"refusing unsafe zip entry name {entry!r}")


def require_clean(tree: Path, src: Path) -> None:
    target = (tree if tree.exists() else src).resolve()
    if not target.is_dir():
        raise Sh3dError(f"source root not found: {target}")
    inside = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, check=False
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise Sh3dError(f"{target} is not inside a git work tree — sh3d_src must be git-tracked")
    status = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain", "--", "."], capture_output=True, text=True, check=True
    )
    if status.stdout.strip():
        raise Sh3dError(f"{target} has uncommitted changes — commit or discard them first")


def pull(roots: Roots, name: str) -> None:
    sh3d = roots.sh3d(name)
    if not sh3d.is_file():
        raise Sh3dError(f"{sh3d} not found")
    tree = roots.tree(name)
    require_clean(tree, roots.src)
    source_sha = sha256_file(sh3d)
    with zipfile.ZipFile(sh3d) as zf:
        infos = zf.infolist()
        for info in infos:
            check_entry_name(info.filename)
        if tree.exists():
            shutil.rmtree(tree)
        tree.mkdir(parents=True)
        for info in infos:
            if info.is_dir() or info.filename in DROPPED:
                continue
            out = tree / info.filename
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(zf.read(info))
    meta = {
        "source_sha256": source_sha,
        "sh3d_version_pinned": SH3D_VERSION_PINNED,
        "entries": [{"name": i.filename, "compress_type": i.compress_type} for i in infos],
    }
    (tree / META).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"pull: ok {name} — {len(infos)} entries → {tree}")


COMMANDS: dict[str, Callable[[Roots, str], None]] = {"pull": pull}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sh3d", description=__doc__)
    parser.add_argument("--homes", type=Path, default=ROOT / "homes")
    parser.add_argument("--src", type=Path, default=ROOT / "sh3d_src")
    parser.add_argument("--backups", type=Path, default=ROOT / "backups")
    parser.add_argument("--work", type=Path, default=ROOT / "work")
    parser.add_argument("--dtd", type=Path, default=ROOT / "work" / "sh3d" / "SweetHome3D.dtd")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in COMMANDS:
        sub.add_parser(cmd).add_argument("name")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    roots = Roots(homes=args.homes, src=args.src, backups=args.backups, work=args.work, dtd=args.dtd)
    try:
        check_name(args.name)
        COMMANDS[args.cmd](roots, args.name)
    except Sh3dError as e:
        print(f"sh3d: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
