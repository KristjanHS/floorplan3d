"""plans.yaml -> per-floor make rules for `make models`; content stamps make each step rerun only on its own inputs."""

import argparse
import json
import os
import re
from pathlib import Path

import export
import plans

STAMPS = plans.WORK / ".stamps"
STATUS = plans.WORK / ".status"
SAFE_PATH = re.compile(r"[A-Za-z0-9_./+-]+")  # nothing make would split, glob or expand
SAFE_NAME = re.compile(r"[A-Za-z0-9_-]+")


def rel(path: Path) -> str:
    s = os.path.relpath(path, plans.ROOT)
    if not SAFE_PATH.fullmatch(s):
        raise ValueError(f"path not make-safe (letters, digits, _ . / + - only): {s}")
    return s


def stamp(path: Path, data: dict) -> Path:
    """Rewrite only on change, so the mtime moves exactly when the step's inputs do."""
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if not path.is_file() or path.read_text() != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return path


def preprocess_inputs(floor: plans.Floor) -> dict:
    st = floor.pdf.stat() if floor.pdf.is_file() else None
    return {
        "pdf": str(floor.pdf),
        "pdf_size": st and st.st_size,
        "pdf_mtime_ns": st and st.st_mtime_ns,
        **{k: getattr(floor, k) for k in ("page", "dpi", "max_side", "min_speck_px", "binarize")},
    }


def export_inputs(floor: plans.Floor) -> dict:
    cal = floor.calibration
    return {
        "calibration": cal and {"p1": cal.p1, "p2": cal.p2, "metres": cal.metres},
        "wall_height_m": floor.wall_height_m,
    }


def report(floor: plans.Floor) -> Path:
    return floor.outdir / f"{export.stem(floor)}_report.json"


def render(floors: list[plans.Floor], plans_file: Path, stamps: Path = STAMPS, status: Path = STATUS) -> str:
    py = f"uv run python scripts/{{}}.py --plans {rel(plans_file)}"
    lines = ["BLENDER ?= blender", ".DELETE_ON_ERROR:", f".PHONY: floors {' '.join(f'done-{f.name}' for f in floors)}"]
    lines.append(f"floors: {' '.join(f'done-{f.name}' for f in floors)}")
    for f in floors:
        if not SAFE_NAME.fullmatch(f.name):
            raise ValueError(f"floor name not make-safe (letters, digits, _ - only): {f.name}")
        pre = rel(stamp(stamps / f"{f.name}.preprocess.json", preprocess_inputs(f)))
        exp = rel(stamp(stamps / f"{f.name}.export.json", export_inputs(f)))
        meta, raw, out = rel(f.meta), rel(f.raw_blend), rel(report(f))
        only = f"--only {f.name}"
        lines += [
            # preprocess writes the .json meta last, so it marks a complete run.
            f"{meta}: {pre} scripts/preprocess.py scripts/plans.py",
            f"\t{py.format('preprocess')} {only}",
            f"{raw}: {meta} scripts/convert.py scripts/ftb_generate.py scripts/sandbox.py",
            f"\t{py.format('convert')} {only} --blender $(BLENDER)",
            f"{out}: {raw} {exp} scripts/export.py scripts/export_blend.py",
            f"\t{py.format('export')} {only} --blender $(BLENDER)",
            f"done-{f.name}: {out}",
            f"\t@mkdir -p {rel(status)} && touch {rel(status / f.name)}",
        ]
    return "\n".join(lines) + "\n"


def summary(floors: list[plans.Floor], status: Path = STATUS) -> tuple[str, int]:
    ok = [f.name for f in floors if (status / f.name).is_file()]
    rows = [f"  {f.name:<12} {'OK' if f.name in ok else 'FAILED'}" for f in floors]
    failed = len(floors) - len(ok)
    return "\n".join([*rows, f"  {len(ok)} OK, {failed} FAILED"]), failed


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plans", type=Path, default=plans.ROOT / "plans.yaml")
    p.add_argument("--out", type=Path, help="write the makefile here (clears per-floor status)")
    p.add_argument("--summary", action="store_true", help="print per-floor OK/FAILED; exit 1 if any failed")
    a = p.parse_args()
    floors = plans.load(a.plans)
    if a.summary:
        text, failed = summary(floors)
        print(text)
        raise SystemExit(1 if failed else 0)
    if a.out is None:
        p.error("--out or --summary required")
    text = render(floors, a.plans.absolute())  # validates names before they become paths below
    for f in floors:
        (STATUS / f.name).unlink(missing_ok=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(text)


if __name__ == "__main__":
    main()
